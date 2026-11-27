import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    alias(libs.plugins.kotlin.jvm)
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

java {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
}

dependencies {
    implementation(libs.json)
    testImplementation(libs.junit.jupiter)
    testRuntimeOnly(libs.junit.platform.launcher)
}

tasks.processResources {
    from("../../film_foundry/assets") { include("edge_sans.txt", "edge_rounded.txt", "date_segments.txt", "date_segments_narrow.txt", "harman_red_125_wedge.txt") }
    from("../../third_party_licenses/ROBOTO_APACHE_2_0.txt") { into("filmfoundry-licenses") }
}

tasks.test {
    useJUnitPlatform()
}
