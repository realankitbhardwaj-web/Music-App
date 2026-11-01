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
KEYSTORE = os.path.join(ROOT, 'vibe-debug.keystore')
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
ICON = os.path.join(ROOT, 'icon.png')
if os.path.exists(ICON):
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        import subprocess
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--quiet', 'pillow'])
        from PIL import Image, ImageDraw
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
else:
    print('no icon.png found - the default icon stays')

# 6) The page asks to be drawn under the status bar (viewport-fit=cover). In the app that made the
#    top bar hide behind the camera/status bar. Without it Android keeps the page between the bars.
www_index = os.path.join(ROOT, 'www', 'index.html')
if os.path.exists(www_index):
    page = read(www_index)
    page = page.replace('viewport-fit=cover, ', '').replace(', viewport-fit=cover', '')
    write(www_index, page)
    print('removed viewport-fit=cover from the app copy of the page')

print('Android project patched OK')
