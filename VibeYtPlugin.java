package com.vibe.music;

import android.content.ComponentName;
import android.content.Context;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import android.util.Base64;

import androidx.annotation.Nullable;
import androidx.core.content.ContextCompat;
import androidx.media3.common.MediaItem;
import androidx.media3.common.MediaMetadata;
import androidx.media3.common.PlaybackException;
import androidx.media3.common.Player;
import androidx.media3.session.MediaController;
import androidx.media3.session.SessionToken;

import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.google.common.util.concurrent.ListenableFuture;

import org.json.JSONObject;
import org.schabi.newpipe.extractor.InfoItem;
import org.schabi.newpipe.extractor.ListExtractor;
import org.schabi.newpipe.extractor.MediaFormat;
import org.schabi.newpipe.extractor.NewPipe;
import org.schabi.newpipe.extractor.Page;
import org.schabi.newpipe.extractor.ServiceList;
import org.schabi.newpipe.extractor.localization.Localization;
import org.schabi.newpipe.extractor.search.SearchExtractor;
import org.schabi.newpipe.extractor.stream.AudioStream;
import org.schabi.newpipe.extractor.stream.DeliveryMethod;
import org.schabi.newpipe.extractor.stream.StreamInfo;
import org.schabi.newpipe.extractor.stream.StreamInfoItem;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;

import okhttp3.OkHttpClient;

/**
 * Bridge between the web app (index.html) and the native player.
 *   play / pause / resume / stop / seek   - transport
 *   setQueue                              - songs to play after the current one (gapless, works with the screen off)
 *   resolve / fetchBase64                 - YouTube link lookup and CORS-free downloading
 *   search                                - YouTube search
 * Events sent to the page: state, ended, transition, remote, error.
 */
@CapacitorPlugin(name = "VibeYt")
public class VibeYtPlugin extends Plugin {

    private static final long YT_TTL_MS = 90L * 60L * 1000L;

    private static final class Resolved {
        String url;
        String mime;
        long ts;
    }

    private static final ConcurrentHashMap<String, Resolved> YT_CACHE = new ConcurrentHashMap<>();
    private static boolean newPipeReady = false;

    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService io = Executors.newCachedThreadPool();
    private final OkHttpClient http = new OkHttpClient.Builder()
            .connectTimeout(20, TimeUnit.SECONDS)
            .readTimeout(60, TimeUnit.SECONDS)
            .build();
    private final List<Consumer<MediaController>> waiting = new ArrayList<>();

    private MediaController controller;
    private ListenableFuture<MediaController> future;
    private long playToken = 0;
    private long queueToken = 0;
    private boolean ticking = false;
    private String endedFor = "";

    // ------------------------------------------------------------------ setup

    @Override
    public void load() {
        VibePlaybackService.remoteListener = action -> {
            JSObject o = new JSObject();
            o.put("action", action);
            notifyListeners("remote", o);
        };
        main.post(this::connect);
    }

    @Override
    protected void handleOnDestroy() {
        VibePlaybackService.remoteListener = null;
        main.removeCallbacksAndMessages(null);
        if (future != null) {
            try { MediaController.releaseFuture(future); } catch (Throwable ignored) { }
        }
        controller = null;
        future = null;
        super.handleOnDestroy();
    }

    private void connect() {
        if (future != null) return;
        Context ctx = getContext();
        SessionToken token = new SessionToken(ctx, new ComponentName(ctx, VibePlaybackService.class));
        future = new MediaController.Builder(ctx, token).buildAsync();
        future.addListener(() -> {
            try {
                controller = future.get();
                controller.addListener(listener);
                List<Consumer<MediaController>> todo = new ArrayList<>(waiting);
                waiting.clear();
                for (Consumer<MediaController> c : todo) c.accept(controller);
            } catch (Throwable e) {
                // could not reach the player service; pending calls time out on their own
            }
        }, ContextCompat.getMainExecutor(ctx));
    }

    /** runs fn on the main thread as soon as the player service is connected */
    private void withController(Consumer<MediaController> fn) {
        main.post(() -> {
            if (controller != null) {
                fn.accept(controller);
            } else {
                waiting.add(fn);
                connect();
            }
        });
    }

    // ------------------------------------------------------------- player events

    private final Player.Listener listener = new Player.Listener() {
        @Override
        public void onIsPlayingChanged(boolean isPlaying) {
            emitState();
            kickTicker();
        }

        @Override
        public void onPlaybackStateChanged(int state) {
            emitState();
            kickTicker();
            if (state == Player.STATE_ENDED && controller != null) {
                MediaItem mi = controller.getCurrentMediaItem();
                String id = mi == null ? "" : mi.mediaId;
                if (!id.equals(endedFor)) {
                    endedFor = id;
                    JSObject o = new JSObject();
                    o.put("id", id);
                    notifyListeners("ended", o);
                }
            }
        }

        @Override
        public void onMediaItemTransition(@Nullable MediaItem mediaItem, int reason) {
            if (mediaItem != null && reason == Player.MEDIA_ITEM_TRANSITION_REASON_AUTO) {
                endedFor = "";
                JSObject o = new JSObject();
                o.put("id", mediaItem.mediaId);
                notifyListeners("transition", o);
            }
        }

        @Override
        public void onPlayerError(PlaybackException error) {
            JSObject o = new JSObject();
            MediaItem mi = controller == null ? null : controller.getCurrentMediaItem();
            o.put("id", mi == null ? "" : mi.mediaId);
            o.put("message", String.valueOf(error.getMessage()));
            notifyListeners("error", o);
        }
    };

    private void emitState() {
        MediaController c = controller;
        if (c == null) return;
        MediaItem mi = c.getCurrentMediaItem();
        int st = c.getPlaybackState();
        long dur = c.getDuration();
        JSObject o = new JSObject();
        o.put("id", mi == null ? "" : mi.mediaId);
        o.put("playing", c.isPlaying());
        o.put("buffering", st == Player.STATE_BUFFERING);
        o.put("ended", st == Player.STATE_ENDED);
        o.put("playWhenReady", c.getPlayWhenReady());
        o.put("pos", Math.max(c.getCurrentPosition(), 0L));
        o.put("dur", dur < 0 ? 0L : dur);
        notifyListeners("state", o);
    }

    private final Runnable tick = new Runnable() {
        @Override
        public void run() {
            MediaController c = controller;
            if (c != null && (c.isPlaying() || c.getPlaybackState() == Player.STATE_BUFFERING)) {
                emitState();
                main.postDelayed(this, 500);
            } else {
                ticking = false;
            }
        }
    };

    private void kickTicker() {
        if (!ticking) {
            ticking = true;
            main.postDelayed(tick, 500);
        }
    }

    // --------------------------------------------------------------- transport

    @PluginMethod
    public void play(final PluginCall call) {
        final String id = call.getString("id", "");
        final String title = call.getString("title", "");
        final String artist = call.getString("artist", "");
        final String artwork = call.getString("artwork", "");
        final String ytId = call.getString("ytId", "");
        final String url = call.getString("url", "");
        final String data = call.getString("data", "");
        final String ext = call.getString("ext", "m4a");
        if (Boolean.TRUE.equals(call.getBoolean("fresh", false)) && !ytId.isEmpty()) YT_CACHE.remove(ytId);
        final long token = ++playToken;
        endedFor = "";

        final AtomicBoolean settled = new AtomicBoolean(false);
        main.postDelayed(() -> {
            if (settled.compareAndSet(false, true)) call.reject("Player did not start in time");
        }, 25000);

        io.execute(() -> {
            String finalUrl = url;
            try {
                if (!ytId.isEmpty()) {
                    finalUrl = resolveYt(ytId).url;
                } else if (!data.isEmpty()) {
                    finalUrl = writeTemp(data, ext);
                }
            } catch (Throwable e) {
                if (settled.compareAndSet(false, true)) call.reject("Could not get audio: " + e.getMessage());
                return;
            }
            if (finalUrl == null || finalUrl.isEmpty()) {
                if (settled.compareAndSet(false, true)) call.reject("No audio address");
                return;
            }
            final String u = finalUrl;
            withController(c -> {
                if (token != playToken) {            // a newer play() already took over
                    if (settled.compareAndSet(false, true)) call.resolve();
                    return;
                }
                c.setMediaItem(buildItem(id, title, artist, artwork, u));
                c.setRepeatMode(Player.REPEAT_MODE_OFF);
                c.prepare();
                c.play();
                if (settled.compareAndSet(false, true)) call.resolve();
            });
        });
    }

    @PluginMethod
    public void pause(final PluginCall call) {
        withController(c -> { c.pause(); call.resolve(); });
    }

    @PluginMethod
    public void resume(final PluginCall call) {
        withController(c -> { c.play(); call.resolve(); });
    }

    @PluginMethod
    public void stop(final PluginCall call) {
        playToken++;
        queueToken++;
        withController(c -> {
            c.stop();
            c.clearMediaItems();
            call.resolve();
        });
    }

    @PluginMethod
    public void seek(final PluginCall call) {
        final double ms = call.getDouble("ms", 0.0);
        withController(c -> { c.seekTo((long) ms); call.resolve(); });
    }

    /**
     * The next few songs, so the player can move on by itself (gapless) even if the
     * phone is locked and the web page is asleep.
     */
    @PluginMethod
    public void setQueue(final PluginCall call) {
        final String afterId = call.getString("afterId", "");
        final boolean repeatOne = Boolean.TRUE.equals(call.getBoolean("repeatOne", false));
        final JSArray arr = call.getArray("items");
        final long token = ++queueToken;

        io.execute(() -> {
            List<MediaItem> list = new ArrayList<>();
            if (arr != null) {
                for (int i = 0; i < arr.length(); i++) {
                    try {
                        JSONObject o = arr.getJSONObject(i);
                        String ytId = o.optString("ytId", "");
                        String url = o.optString("url", "");
                        if (!ytId.isEmpty()) url = resolveYt(ytId).url;
                        if (url.isEmpty()) continue;
                        list.add(buildItem(o.optString("id", ""), o.optString("title", ""),
                                o.optString("artist", ""), o.optString("artwork", ""), url));
                    } catch (Throwable ignored) {
                        // skip a song we could not look up; the app falls back to playing it directly later
                        break;
                    }
                }
            }
            withController(c -> {
                if (token != queueToken) { call.resolve(); return; }
                MediaItem cur = c.getCurrentMediaItem();
                if (cur == null || (!afterId.isEmpty() && !afterId.equals(cur.mediaId))) {
                    call.resolve();                   // the song changed meanwhile; the app will send a fresh queue
                    return;
                }
                c.setRepeatMode(repeatOne ? Player.REPEAT_MODE_ONE : Player.REPEAT_MODE_OFF);
                int idx = c.getCurrentMediaItemIndex();
                int count = c.getMediaItemCount();
                if (idx + 1 < count) c.removeMediaItems(idx + 1, count);
                if (!list.isEmpty()) c.addMediaItems(list);
                call.resolve();
            });
        });
    }

    // ------------------------------------------------------------ YouTube / net

    @PluginMethod
    public void resolve(final PluginCall call) {
        final String ytId = call.getString("ytId", "");
        io.execute(() -> {
            try {
                Resolved r = resolveYt(ytId);
                JSObject o = new JSObject();
                o.put("url", r.url);
                o.put("mime", r.mime);
                call.resolve(o);
            } catch (Throwable e) {
                call.reject(String.valueOf(e.getMessage()));
            }
        });
    }

    @PluginMethod
    public void fetchBase64(final PluginCall call) {
        final String url = call.getString("url", "");
        io.execute(() -> {
            try {
                okhttp3.Request req = new okhttp3.Request.Builder()
                        .url(url)
                        .header("User-Agent", NewPipeDownloader.USER_AGENT)
                        .build();
                try (okhttp3.Response r = http.newCall(req).execute()) {
                    if (!r.isSuccessful() || r.body() == null) throw new IOException("HTTP " + r.code());
                    byte[] bytes = r.body().bytes();
                    if (bytes.length > 80 * 1024 * 1024) throw new IOException("file too big");
                    JSObject o = new JSObject();
                    o.put("data", Base64.encodeToString(bytes, Base64.NO_WRAP));
                    String ct = r.header("Content-Type");
                    o.put("mime", ct == null ? "audio/mp4" : ct);
                    call.resolve(o);
                }
            } catch (Throwable e) {
                call.reject(String.valueOf(e.getMessage()));
            }
        });
    }

    @PluginMethod
    public void search(final PluginCall call) {
        final String q = call.getString("q", "");
        final Integer pageArg = call.getInt("page", 1);
        final int page = pageArg == null ? 1 : Math.max(1, pageArg);
        io.execute(() -> {
            try {
                ensureNewPipe();
                SearchExtractor ex = ServiceList.YouTube.getSearchExtractor(q);
                ex.fetchPage();
                ListExtractor.InfoItemsPage<InfoItem> pg = ex.getInitialPage();
                for (int i = 1; i < page && pg != null; i++) {
                    Page np = pg.getNextPage();
                    pg = (np == null) ? null : ex.getPage(np);
                }
                JSArray arr = new JSArray();
                if (pg != null) {
                    for (InfoItem it : pg.getItems()) {
                        if (!(it instanceof StreamInfoItem)) continue;
                        StreamInfoItem s = (StreamInfoItem) it;
                        String vid = Uri.parse(s.getUrl()).getQueryParameter("v");
                        if (vid == null || vid.isEmpty()) continue;
                        JSObject o = new JSObject();
                        o.put("id", vid);
                        o.put("title", s.getName());
                        o.put("author", s.getUploaderName() == null ? "" : s.getUploaderName());
                        o.put("dur", s.getDuration());
                        arr.put(o);
                    }
                }
                JSObject res = new JSObject();
                res.put("items", arr);
                call.resolve(res);
            } catch (Throwable e) {
                call.reject(String.valueOf(e.getMessage()));
            }
        });
    }

    // ----------------------------------------------------------------- helpers

    private static synchronized void ensureNewPipe() {
        if (!newPipeReady) {
            NewPipe.init(new NewPipeDownloader(), new Localization("en", "IN"));
            newPipeReady = true;
        }
    }

    private Resolved resolveYt(String id) throws Exception {
        long now = System.currentTimeMillis();
        Resolved hit = YT_CACHE.get(id);
        if (hit != null && now - hit.ts < YT_TTL_MS) return hit;

        ensureNewPipe();
        StreamInfo info = StreamInfo.getInfo(ServiceList.YouTube, "https://www.youtube.com/watch?v=" + id);
        AudioStream best = null;
        int bestScore = Integer.MIN_VALUE;
        for (AudioStream a : info.getAudioStreams()) {
            if (!a.isUrl() || a.getDeliveryMethod() != DeliveryMethod.PROGRESSIVE_HTTP) continue;
            int score = a.getAverageBitrate() + (a.getFormat() == MediaFormat.M4A ? 100000 : 0);
            if (score > bestScore) {
                best = a;
                bestScore = score;
            }
        }
        if (best == null) throw new Exception("No audio stream found for this video");

        Resolved r = new Resolved();
        r.url = best.getContent();
        r.mime = best.getFormat() == null ? "audio/mp4" : best.getFormat().getMimeType();
        r.ts = now;
        YT_CACHE.put(id, r);
        return r;
    }

    private MediaItem buildItem(String id, String title, String artist, String artwork, String url) {
        MediaMetadata.Builder md = new MediaMetadata.Builder().setTitle(title).setArtist(artist);
        if (artwork != null && artwork.startsWith("http")) md.setArtworkUri(Uri.parse(artwork));
        return new MediaItem.Builder()
                .setMediaId(id)
                .setUri(url)
                .setMediaMetadata(md.build())
                .build();
    }

    private String writeTemp(String b64, String ext) throws IOException {
        File dir = getContext().getCacheDir();
        File[] old = dir.listFiles((d, n) -> n.startsWith("play_"));
        if (old != null) {
            for (File f : old) {
                //noinspection ResultOfMethodCallIgnored
                f.delete();
            }
        }
        String safe = ext == null ? "" : ext.replaceAll("[^a-zA-Z0-9]", "");
        if (safe.isEmpty()) safe = "m4a";
        File f = new File(dir, "play_" + System.nanoTime() + "." + safe);
        byte[] bytes = Base64.decode(b64, Base64.DEFAULT);
        try (FileOutputStream out = new FileOutputStream(f)) {
            out.write(bytes);
        }
        return Uri.fromFile(f).toString();
    }
}
