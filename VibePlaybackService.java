package com.vibe.music;

import android.content.Intent;

import androidx.annotation.Nullable;
import androidx.media3.common.AudioAttributes;
import androidx.media3.common.C;
import androidx.media3.common.ForwardingPlayer;
import androidx.media3.common.Player;
import androidx.media3.datasource.DefaultHttpDataSource;
import androidx.media3.exoplayer.ExoPlayer;
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory;
import androidx.media3.session.MediaSession;
import androidx.media3.session.MediaSessionService;

/**
 * The real music player. It lives in a foreground service, so the song keeps playing
 * with the screen locked, and Android shows the lock-screen / notification controls.
 */
public class VibePlaybackService extends MediaSessionService {

    public interface RemoteListener { void onRemote(String action); }

    /** set by the plugin; called when the user taps next / previous in the notification */
    static volatile RemoteListener remoteListener;

    private MediaSession session;
    private ExoPlayer exo;

    private static void fire(String action) {
        RemoteListener l = remoteListener;
        if (l != null) {
            try { l.onRemote(action); } catch (Throwable ignored) { }
        }
    }

    @Override
    public void onCreate() {
        super.onCreate();

        DefaultHttpDataSource.Factory http = new DefaultHttpDataSource.Factory()
                .setUserAgent("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36")
                .setAllowCrossProtocolRedirects(true)
                .setConnectTimeoutMs(15000)
                .setReadTimeoutMs(20000);

        exo = new ExoPlayer.Builder(this)
                .setMediaSourceFactory(new DefaultMediaSourceFactory(this).setDataSourceFactory(http))
                .setAudioAttributes(new AudioAttributes.Builder()
                        .setUsage(C.USAGE_MEDIA)
                        .setContentType(C.AUDIO_CONTENT_TYPE_MUSIC)
                        .build(), true)
                .setHandleAudioBecomingNoisy(true)
                .setWakeMode(C.WAKE_MODE_NETWORK)
                .build();

        // The app (not the player) knows what the "next song" is, so next / previous
        // taps are passed to the app instead of moving inside the player.
        Player wrapped = new ForwardingPlayer(exo) {
            @Override
            public Player.Commands getAvailableCommands() {
                return super.getAvailableCommands().buildUpon()
                        .add(Player.COMMAND_SEEK_TO_NEXT)
                        .add(Player.COMMAND_SEEK_TO_PREVIOUS)
                        .add(Player.COMMAND_SEEK_TO_NEXT_MEDIA_ITEM)
                        .add(Player.COMMAND_SEEK_TO_PREVIOUS_MEDIA_ITEM)
                        .build();
            }

            @Override
            public boolean isCommandAvailable(int command) {
                return getAvailableCommands().contains(command);
            }

            @Override public void seekToNext() { fire("next"); }
            @Override public void seekToPrevious() { fire("previous"); }
            @Override public void seekToNextMediaItem() { fire("next"); }
            @Override public void seekToPreviousMediaItem() { fire("previous"); }
        };

        session = new MediaSession.Builder(this, wrapped).build();
    }

    @Nullable
    @Override
    public MediaSession onGetSession(MediaSession.ControllerInfo controllerInfo) {
        return session;
    }

    @Override
    public void onTaskRemoved(@Nullable Intent rootIntent) {
        // app swiped away from recents -> stop the music too
        pauseAllPlayersAndStopSelf();
    }

    @Override
    public void onDestroy() {
        if (session != null) {
            session.getPlayer().release();
            session.release();
            session = null;
        }
        super.onDestroy();
    }
}
