"""Collect Android runtime notices from resolved coordinates and the local Gradle cache.
Run :app:exportReleaseDependencyInventory first; even its stale-notices failure
writes app/build/reports/release-runtime.txt. No private paths or binaries copied.
"""
from pathlib import Path
import argparse
import io
import json
import os
import shutil
import xml.etree.ElementTree as ET
import zipfile


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--coordinates', type=Path, default=root/'android/app/build/reports/release-runtime.txt')
    parser.add_argument('--cache', type=Path, default=Path(os.environ.get('GRADLE_USER_HOME', Path.home()/'.gradle'))/'caches/modules-2/files-2.1')
    args = parser.parse_args()
    dest = root/'third_party_licenses/android'
    for folder in ('poms', 'artifact-notices', 'assets', 'upstream'):
        (dest/folder).mkdir(parents=True, exist_ok=True)
    ns = {'m': 'http://maven.apache.org/POM/4.0.0'}

    def metadata(coordinate):
        group, artifact, version = coordinate.split(':')
        directory = args.cache/group/artifact/version
        pom = next(directory.rglob('*.pom'))
        tree = ET.parse(pom)
        shutil.copyfile(pom, dest/'poms'/f'{group}__{artifact}__{version}.pom')
        licenses = [{'name': x.findtext('m:name', namespaces=ns), 'url': x.findtext('m:url', namespaces=ns)}
                    for x in tree.findall('m:licenses/m:license', ns)]
        if not licenses:
            parent = tree.find('m:parent', ns)
            if parent is not None:
                parent_id = ':'.join(parent.findtext('m:'+key, namespaces=ns) for key in ('groupId','artifactId','version'))
                licenses = metadata(parent_id)[0]
        if not licenses:
            raise RuntimeError(f'No license metadata: {coordinate}')
        return licenses, directory

    coordinates = sorted(set(args.coordinates.read_text(encoding='utf8').split()))
    entries, notices = [], {}
    for coordinate in coordinates:
        group, artifact, version = coordinate.split(':')
        licenses, directory = metadata(coordinate)
        base = 'https://dl.google.com/dl/android/maven2/' if group.startswith('androidx.') else 'https://repo.maven.apache.org/maven2/'
        source_url = base + f'{group.replace(".", "/")}/{artifact}/{version}/{artifact}-{version}-sources.jar'
        native = []
        artifacts = sorted(p for p in [*directory.rglob('*.aar'), *directory.rglob('*.jar')]
                           if not p.name.endswith(('-sources.jar', '-javadoc.jar')))
        for artifact_file in artifacts:
            def collect(archive, prefix=''):
                for member in archive.namelist():
                    if member.endswith('.so'):
                        native.append(member)
                    basename = Path(member).name.upper()
                    if basename.startswith(('LICENSE','LICENCE','NOTICE','COPYING','COPYRIGHT','AUTHORS')) and not member.endswith('/'):
                        name = coordinate.replace(':','__')+'__'+(prefix+member).replace('/','__')+'.txt'
                        value = archive.read(member).decode('utf8')
                        notices[name] = value
                        # Keep public paths short enough for ordinary Windows checkouts.
                        public_name = f'{artifact}-{version}-{len(notices):02d}.txt'
                        (dest/'artifact-notices'/public_name).write_text(value, encoding='utf8')
                    if member == 'classes.jar':
                        with zipfile.ZipFile(io.BytesIO(archive.read(member))) as nested:
                            collect(nested, 'classes.jar/')
            with zipfile.ZipFile(artifact_file) as archive:
                collect(archive)
        entries.append({'coordinate': coordinate, 'licenses': licenses, 'sources': source_url,
                        'artifact_names': [p.name for p in artifacts], 'native_libraries': native,
                        'kind': 'runtime artifact' if artifacts else 'metadata / platform constraint'})
    apache = next(value for value in notices.values() if 'Apache License' in value and 'Version 2.0' in value)
    (dest/'upstream/APACHE_2_0.txt').write_text(apache, encoding='utf8')
    for name in ['JSON_JAVA_PUBLIC_DOMAIN.txt','LIBYUV_BSD.txt','LIBYUV_PATENTS.txt','LIBYUV_AUTHORS.txt']:
        if not (dest/'upstream'/name).is_file():
            raise RuntimeError(f'Retain original upstream notice first: {name}')
    (dest/'DEPENDENCIES.txt').write_text('\n'.join(coordinates)+'\n', encoding='utf8')
    (dest/'runtime.json').write_text(json.dumps(entries, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    lines = ['# Android 0.1.0 runtime dependency inventory', '',
             'Resolved from app:releaseRuntimeClasspath; metadata-only components are distinguished from runtime artifacts.',
             'Original POMs, including inherited licenses, are in poms/. Original archive notices are in artifact-notices/.',
             'Source URLs in runtime.json are upstream locations; BOM-only modules may have no source archive.', '',
             '| Component | Kind | Declared licenses |', '| --- | --- | --- |']
    for row in entries:
        lines.append(f"| {row['coordinate']} | {row['kind']} | {'; '.join(x['name'] for x in row['licenses'])} |")
    lines += ['', 'CameraX camera-core 1.5.3 contributes libimage_processing_util_jni.so and libsurface_util_jni.so for each ABI.',
              'Its POM declares libyuv BSD licensing; retain upstream/LIBYUV_BSD.txt, LIBYUV_PATENTS.txt and LIBYUV_AUTHORS.txt.',
              'Upstream POM reference: https://chromium.googlesource.com/libyuv/libyuv/+/refs/heads/main/README.chromium .',
              'These libyuv notices were retrieved from that upstream main branch on 2026-11-02, not a reconstruction of its native build commit.',
              'JSON-java license: https://github.com/stleary/JSON-java/blob/20240303/LICENSE .',
              'Gradle, AGP, SDK, JUnit and instrumentation-only dependencies are not APK runtime components.',
              'Re-resolve and regenerate when changing dependencies; no private signing material belongs here.', '']
    (dest/'README.md').write_text('\n'.join(lines), encoding='utf8')
    combined = ['Film Foundry - GPL-3.0-or-later', 'Copyright soundsec', '', (root/'COPYING').read_text(encoding='utf8'),
                '\nANDROID RUNTIME DEPENDENCIES\n', '\n'.join(lines),
                '\nProject source: https://github.com/soundsec/Film-Foundry\n',
                'Component source locations:\n'+'\n'.join(f"{r['coordinate']}: {r['sources']}" for r in entries)]
    for path in sorted((dest/'upstream').glob('*.txt')):
        combined += ['\n'+path.name+'\n',path.read_text(encoding='utf8')]
    for name,value in sorted(notices.items()):
        combined += ['\n'+name+'\n',value]
    (dest/'assets/third_party_notices.txt').write_text('\n'.join(combined), encoding='utf8')
    print(f'Collected {len(entries)} components and {len(notices)} archive notices; no binaries or private paths copied.')


if __name__ == '__main__':
    main()
