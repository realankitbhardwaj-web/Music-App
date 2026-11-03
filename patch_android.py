#!/usr/bin/env python3
"""
Runs inside the GitHub build (after `npx cap add android`).
Copies the native player into the Android project and wires it up
(permissions, service, libraries). Nothing here needs to be edited by hand.
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
ANDROID = os.path.join(ROOT, 'android')
JAVA_DIR = os.path.join(ANDROID, 'app', 'src', 'main', 'java', 'com', 'vibe', 'music')
JAVA_FILES = ['MainActivity.java', 'VibePlaybackService.java', 'VibeYtPlugin.java', 'NewPipeDownloader.java']


def read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def write(path, text):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def replace_once(text, old, new, label):
    if old not in text:
        print('PATCH FAILED (%s): could not find expected text' % label)
        sys.exit(1)
    return text.replace(old, new, 1)


# 1) native source files ----------------------------------------------------------------
os.makedirs(JAVA_DIR, exist_ok=True)
for name in JAVA_FILES:
    shutil.copyfile(os.path.join(ROOT, name), os.path.join(JAVA_DIR, name))
print('copied', ', '.join(JAVA_FILES))

# 2) AndroidManifest.xml ----------------------------------------------------------------
mf_path = os.path.join(ANDROID, 'app', 'src', 'main', 'AndroidManifest.xml')
mf = read(mf_path)
mf = replace_once(mf, 'android:allowBackup="true"',
                  'android:allowBackup="true"\n        android:usesCleartextTraffic="true"', 'manifest application')
mf = replace_once(mf, '        <provider',
                  '''        <service
            android:name=".VibePlaybackService"
            android:exported="true"
            android:foregroundServiceType="mediaPlayback">
            <intent-filter>
                <action android:name="androidx.media3.session.MediaSessionService" />
            </intent-filter>
        </service>

        <provider''', 'manifest service')
mf = replace_once(mf, '    <uses-permission android:name="android.permission.INTERNET" />',
                  '''    <uses-permission android:name="android.permission.INTERNET" />
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE" />
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE_MEDIA_PLAYBACK" />
    <uses-permission android:name="android.permission.POST_NOTIFICATIONS" />
    <uses-permission android:name="android.permission.WAKE_LOCK" />''', 'manifest permissions')
write(mf_path, mf)

# 3) root build.gradle: JitPack (where the NewPipe extractor lives) -----------------------
rb_path = os.path.join(ANDROID, 'build.gradle')
rb = read(rb_path)
rb = replace_once(rb, 'allprojects {\n    repositories {\n        google()\n        mavenCentral()\n',
                  "allprojects {\n    repositories {\n        google()\n        mavenCentral()\n        maven { url 'https://jitpack.io' }\n",
                  'root build.gradle')
write(rb_path, rb)

# Same signing key on every build, so a new APK installs OVER the old app (keeps your data).
# Without this, GitHub makes a brand-new key each time and Android says "App not installed".
KEYSTORE_B64 = (
    "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"
)

KEYSTORE = os.path.join(ROOT, 'vibe-debug.keystore')
if not os.path.exists(KEYSTORE):
    # The signing key is also kept inside this script, so the repo does not need the binary file.
    import base64
    with open(KEYSTORE, 'wb') as f:
        f.write(base64.b64decode(KEYSTORE_B64))
    print('vibe-debug.keystore was not in the repo - recreated it from the copy inside this script')
if os.path.exists(KEYSTORE):
    SIGNING = """    signingConfigs {
        debug {
            storeFile file('../../vibe-debug.keystore')
            storePassword 'android'
            keyAlias 'vibe'
            keyPassword 'android'
        }
    }
"""
    print('using the fixed signing key (vibe-debug.keystore)')
else:
    SIGNING = ''
    print('WARNING: vibe-debug.keystore not found - the APK will get a random key and cannot update the old app')

# 4) app/build.gradle: libraries + desugaring --------------------------------------------
ab_path = os.path.join(ANDROID, 'app', 'build.gradle')
ab = read(ab_path)
ab = replace_once(ab, '    namespace = "com.vibe.music"\n',
                  '''    namespace = "com.vibe.music"
    compileOptions {
        coreLibraryDesugaringEnabled true
        sourceCompatibility JavaVersion.VERSION_21
        targetCompatibility JavaVersion.VERSION_21
    }
''' + SIGNING, 'app build.gradle compileOptions')
ab = replace_once(ab, "    implementation fileTree(include: ['*.jar'], dir: 'libs')\n",
                  """    implementation fileTree(include: ['*.jar'], dir: 'libs')
    implementation 'androidx.media3:media3-exoplayer:1.8.0'
    implementation 'androidx.media3:media3-session:1.8.0'
    implementation 'com.squareup.okhttp3:okhttp:4.12.0'
    implementation 'com.github.teamnewpipe:NewPipeExtractor:v0.26.5'
    coreLibraryDesugaring 'com.android.tools:desugar_jdk_libs_nio:2.1.5'
""", 'app build.gradle dependencies')
write(ab_path, ab)

# 4b) Fresh package id. Some phones keep leftovers of an earlier install of the same id and then
#     refuse every update with "App not installed". A new id (only the id, nothing else changes)
#     installs next to anything that is left over.
ab = read(ab_path)
ab = replace_once(ab, 'applicationId "com.vibe.music"', 'applicationId "com.ankit.vibeplayer"', 'applicationId')
write(ab_path, ab)
print('app id is now com.ankit.vibeplayer')

# 5) more memory for the build ------------------------------------------------------------
gp_path = os.path.join(ANDROID, 'gradle.properties')
gp = read(gp_path)
gp = replace_once(gp, 'org.gradle.jvmargs=-Xmx1536m', 'org.gradle.jvmargs=-Xmx3g -Dfile.encoding=UTF-8', 'gradle.properties')
write(gp_path, gp)

# 5b) App icon: icon.png (any square picture, 1024x1024 is best) becomes the launcher icon.
#     This step can never stop the build: if anything goes wrong the default icon is kept.
def load_pil():
    try:
        from PIL import Image, ImageDraw
        return Image, ImageDraw
    except ImportError:
        pass
    import subprocess
    # Ubuntu 24.04 refuses a plain "pip install", so try the variants that are allowed
    for extra in (['--break-system-packages'], ['--user', '--break-system-packages'], ['--user'], []):
        subprocess.call([sys.executable, '-m', 'pip', 'install', '--quiet', 'pillow'] + extra)
        try:
            from PIL import Image, ImageDraw
            return Image, ImageDraw
        except ImportError:
            continue
    return None

ICON = os.path.join(ROOT, 'icon.png')
try:
    if not os.path.exists(ICON):
        print('no icon.png found - the default icon stays')
    else:
        pil = load_pil()
        if pil is None:
            print('WARNING: could not install the image library - the default icon stays')
        else:
            Image, ImageDraw = pil
            RES = os.path.join(ANDROID, 'app', 'src', 'main', 'res')
            master = Image.open(ICON).convert('RGBA')
            # density: (old-style icon px, adaptive icon layer px)
            for dens, (leg, adp) in {'mdpi': (48, 108), 'hdpi': (72, 162), 'xhdpi': (96, 216),
                                     'xxhdpi': (144, 324), 'xxxhdpi': (192, 432)}.items():
                folder = os.path.join(RES, 'mipmap-' + dens)
                os.makedirs(folder, exist_ok=True)
                big = leg * 4
                base = master.resize((big, big), Image.LANCZOS)
                sq = Image.new('L', (big, big), 0)
                ImageDraw.Draw(sq).rounded_rectangle((0, 0, big - 1, big - 1), radius=int(big * 0.22), fill=255)
                circ = Image.new('L', (big, big), 0)
                ImageDraw.Draw(circ).ellipse((0, 0, big - 1, big - 1), fill=255)
                a = base.copy(); a.putalpha(sq)
                a.resize((leg, leg), Image.LANCZOS).save(os.path.join(folder, 'ic_launcher.png'))
                b = base.copy(); b.putalpha(circ)
                b.resize((leg, leg), Image.LANCZOS).save(os.path.join(folder, 'ic_launcher_round.png'))
                master.resize((adp, adp), Image.LANCZOS).save(os.path.join(folder, 'ic_launcher_foreground.png'))
            bg = os.path.join(RES, 'values', 'ic_launcher_background.xml')
            if os.path.exists(bg):
                write(bg, read(bg).replace('#FFFFFF', '#B8237F'))
            print('app icon set from icon.png')
except Exception as e:
    print('WARNING: icon step skipped (%s) - the default icon stays' % e)

# 6) The page asks to be drawn under the status bar (viewport-fit=cover). In the app that made the
#    top bar hide behind the camera/status bar. Without it Android keeps the page between the bars.
www_index = os.path.join(ROOT, 'www', 'index.html')
if os.path.exists(www_index):
    page = read(www_index)
    page = page.replace('viewport-fit=cover, ', '').replace(', viewport-fit=cover', '')
    write(www_index, page)
    print('removed viewport-fit=cover from the app copy of the page')

print('Android project patched OK')
