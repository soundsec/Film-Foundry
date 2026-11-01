# Android 0.1.0 runtime dependency inventory

Resolved from app:releaseRuntimeClasspath; metadata-only components are distinguished from runtime artifacts.
Original POMs, including inherited licenses, are in poms/. Original archive notices are in artifact-notices/.
Source URLs in runtime.json are upstream locations; BOM-only modules may have no source archive.

| Component | Kind | Declared licenses |
| --- | --- | --- |
| androidx.activity:activity:1.9.3 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.annotation:annotation-experimental:1.4.1 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.annotation:annotation-jvm:1.8.1 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.annotation:annotation:1.8.1 | metadata / platform constraint | The Apache Software License, Version 2.0 |
| androidx.appcompat:appcompat-resources:1.1.0 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.appcompat:appcompat:1.1.0 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.arch.core:core-common:2.2.0 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.arch.core:core-runtime:2.2.0 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.camera.featurecombinationquery:featurecombinationquery:1.5.3 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.camera:camera-camera2:1.5.3 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.camera:camera-core:1.5.3 | runtime artifact | The Apache Software License, Version 2.0; BSD License |
| androidx.camera:camera-lifecycle:1.5.3 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.camera:camera-video:1.5.3 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.camera:camera-view:1.5.3 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.collection:collection:1.1.0 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.concurrent:concurrent-futures-ktx:1.1.0 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.concurrent:concurrent-futures:1.1.0 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.core:core-ktx:1.13.0 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.core:core:1.13.0 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.cursoradapter:cursoradapter:1.0.0 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.customview:customview:1.0.0 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.drawerlayout:drawerlayout:1.0.0 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.exifinterface:exifinterface:1.4.2 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.fragment:fragment:1.1.0 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.interpolator:interpolator:1.0.0 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.lifecycle:lifecycle-common:2.6.2 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.lifecycle:lifecycle-livedata-core:2.6.2 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.lifecycle:lifecycle-livedata:2.6.2 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.lifecycle:lifecycle-runtime:2.6.2 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.lifecycle:lifecycle-viewmodel-savedstate:2.6.2 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.lifecycle:lifecycle-viewmodel:2.6.2 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.loader:loader:1.0.0 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.profileinstaller:profileinstaller:1.3.1 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.savedstate:savedstate:1.2.1 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.startup:startup-runtime:1.1.1 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.tracing:tracing-ktx:1.2.0 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.tracing:tracing:1.2.0 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.vectordrawable:vectordrawable-animated:1.1.0 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.vectordrawable:vectordrawable:1.1.0 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.versionedparcelable:versionedparcelable:1.1.1 | runtime artifact | The Apache Software License, Version 2.0 |
| androidx.viewpager:viewpager:1.0.0 | runtime artifact | The Apache Software License, Version 2.0 |
| com.google.auto.value:auto-value-annotations:1.6.3 | runtime artifact | Apache 2.0 |
| com.google.guava:listenablefuture:1.0 | runtime artifact | The Apache Software License, Version 2.0 |
| org.jetbrains.kotlin:kotlin-stdlib:2.2.20 | runtime artifact | Apache-2.0 |
| org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0 | runtime artifact | The Apache Software License, Version 2.0 |
| org.jetbrains.kotlinx:kotlinx-coroutines-bom:1.9.0 | metadata / platform constraint | The Apache Software License, Version 2.0 |
| org.jetbrains.kotlinx:kotlinx-coroutines-core-jvm:1.9.0 | runtime artifact | The Apache Software License, Version 2.0 |
| org.jetbrains.kotlinx:kotlinx-coroutines-core:1.9.0 | metadata / platform constraint | The Apache Software License, Version 2.0 |
| org.jetbrains:annotations:23.0.0 | runtime artifact | The Apache Software License, Version 2.0 |
| org.json:json:20240303 | runtime artifact | Public Domain |
| org.jspecify:jspecify:1.0.0 | runtime artifact | The Apache License, Version 2.0 |

CameraX camera-core 1.5.3 contributes libimage_processing_util_jni.so and libsurface_util_jni.so for each ABI.
Its POM declares libyuv BSD licensing; retain upstream/LIBYUV_BSD.txt, LIBYUV_PATENTS.txt and LIBYUV_AUTHORS.txt.
Upstream POM reference: https://chromium.googlesource.com/libyuv/libyuv/+/refs/heads/main/README.chromium .
These libyuv notices were retrieved from that upstream main branch on 2026-11-02, not a reconstruction of its native build commit.
JSON-java license: https://github.com/stleary/JSON-java/blob/20240303/LICENSE .
Gradle, AGP, SDK, JUnit and instrumentation-only dependencies are not APK runtime components.
Re-resolve and regenerate when changing dependencies; no private signing material belongs here.
