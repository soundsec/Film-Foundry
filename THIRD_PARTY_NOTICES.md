# Third-party sources and acknowledgements

Film Foundry is distributed under GPL-3.0-or-later for the project's own code.
The items below distinguish algorithm sources from design references and
separately installed dependencies. This document is not a binary dependency
inventory or a statement that every provenance question has been resolved.

## Algorithms

- **Tanner Helland — Kelvin-to-RGB approximation.** The coefficients and
  piecewise fit in `film_foundry/core/scanner.py:color_temperature_rgb` follow
  [Helland's 2012 algorithm](https://tannerhelland.com/2012/09/18/convert-temperature-rgb-algorithm-code.html).
  Film Foundry adds its own range handling and normalization. The author's
  [code licensing page](https://tannerhelland.com/code.html) identifies the
  BSD license; the complete upstream notice is retained in
  [third_party_licenses/TANNER_HELLAND_BSD_2_CLAUSE.txt](third_party_licenses/TANNER_HELLAND_BSD_2_CLAUSE.txt).
- **Chris Wellons — lowbias32.** The integer mixer in
  `film_foundry/core/silver_grain.py:_mix_u32` and
  `film_foundry/core/antibanding.py:_dither_mix` correspond to lowbias32 from
  [hash-prospector](https://github.com/skeeto/hash-prospector). The surrounding
  grain model and dither coordinate composition are Film Foundry code. The upstream Unlicense is retained in
  [third_party_licenses/HASH_PROSPECTOR_UNLICENSE.txt](third_party_licenses/HASH_PROSPECTOR_UNLICENSE.txt).

## Anti-banding replacement (2026-11-02)

The earlier coordinate expression with unresolved acquisition history has been
removed from the current implementation. Output dither now uses the explicitly
credited lowbias32 implementation above, with separately mixed global axes and
channel salts. Its noise amplitude, smooth-area mask and wrapped-field averaging
are retained; the exact noise realization changes when 8-bit anti-banding is on.
This replacement does not alter developed media or 16-bit output. It does not
retroactively resolve the provenance of older source snapshots or binaries.

## Design references

[SpektraLab](https://github.com/JamesQiu2005/SpektraLab) informed discussions
of stage separation, caching, physical scales and observation reuse. It is
not a bundled dependency. The source review found no imported SpektraLab
engine, profile, LUT or image asset. This statement describes that review's
scope rather than proving the absence of every possible external influence.

Research and design references also include spektrafilm, Fotufilm and the
Smits RGB-to-spectrum method. See [acknowledgements](docs/REFERENCE.md#acknowledgements)
and [desktop performance strategies](docs/REFERENCE.md#performance) for
the specific references and local implementation boundaries. These design
acknowledgements do not replace the algorithm notices above.

Other photographic comparisons used for relative output tones are credited
in [scan-reference notes](docs/DESKTOP_GUIDE.md#scan-tones); their photographs
are not included in this source tree.

## Dependencies and binary distribution

The Python environment declares NumPy, OpenCV, Pillow, python-lcms2 and
PySide6-Essentials; optional video decoding uses PyAV. These packages and
their bundled libraries retain their own licenses. Installing dependencies
does not transfer their copyrights to Film Foundry.

The current desktop build's dependency metadata and supplied license texts
are retained under [third_party_licenses/runtime](third_party_licenses/runtime/); the version
and license summary is in [third_party_licenses/README.md](third_party_licenses/README.md).
This includes Python and the PyInstaller bootloader terms. Permissive
licenses still have notice conditions. python-lcms2 is GPL-licensed, and
Qt/PySide6/Shiboken offer LGPL/GPL options; this GPL release uses their
GPLv3 option. Design references alone do not create bundled dependencies.

The rebuilt Windows package also records its actual native-file table in
[third_party_licenses/windows-native](third_party_licenses/windows-native/).
Original notices from the nine matching Conda package builds are retained there.
Native resolution is restricted to the selected Python environment and Windows;
unrelated tools on the caller's PATH are not intended binary inputs.

A binary distributor must inventory the actual bundled versions, preserve
the applicable license texts/notices and meet their source obligations.
The source notices here do not certify an EXE, DLL set or APK. A commercial
agreement with the Film Foundry author covers only rights that author can
grant and does not replace third-party terms.

## Android source and build dependencies

Android dependency coordinates and declared versions are recorded in
`android/gradle/libs.versions.toml` and each module's `build.gradle.kts`.
They include Kotlin, kotlinx.coroutines, AndroidX (CameraX, Lifecycle and
Activity), org.json, and JUnit for tests. The Android Gradle plugin and Gradle
are build tools. These upstream projects retain their own copyrights and
license conditions; the desktop runtime inventory above does not cover an APK.

The source tree includes the Gradle wrapper JAR. The matching Gradle 9.6.0
distribution's original license and notice are retained under
[third_party_licenses/gradle](third_party_licenses/gradle/). That distribution
notice also describes components of Gradle itself; it is not an APK inventory.
SDK installations, dependency caches, signing keys and local device results
are excluded from the prepared source tree.

The Android 0.1.0 release runtime graph is recorded in
[third_party_licenses/android/README.md](third_party_licenses/android/README.md).
It includes all 51 resolved components (including metadata constraints), their
original POMs and inherited license declarations, original AAR notices,
JSON-java's public-domain notice, and libyuv's BSD/patent/author notices.
CameraX camera-core supplies the two native libraries for each ABI. The same
license text is packaged in the APK and readable from Settings > About >
Open-source licenses. A release build checks that the recorded dependency
coordinates match the resolved graph; changing dependencies requires refreshing
the collected notices. The JDK/Gradle/SDK and test tools are separate build-time
dependencies, not part of this runtime list.
