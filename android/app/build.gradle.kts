import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    alias(libs.plugins.android.application)
}

// Secrets belong to the invoking environment, never to the source checkout.
val releaseStore = providers.environmentVariable("FILM_RELEASE_STORE_FILE").orNull
val releaseStorePassword = providers.environmentVariable("FILM_RELEASE_STORE_PASSWORD").orNull
val releaseAlias = providers.environmentVariable("FILM_RELEASE_KEY_ALIAS").orNull
val releaseKeyPassword = providers.environmentVariable("FILM_RELEASE_KEY_PASSWORD").orNull
val releaseCredentials = listOf(releaseStore, releaseStorePassword, releaseAlias, releaseKeyPassword)
require(releaseCredentials.all { it == null } || releaseCredentials.all { !it.isNullOrBlank() }) {
    "Set all four FILM_RELEASE_* signing variables, or leave all unset."
}

android {
    namespace = "com.filmfoundry.mobile"
    compileSdk = 37
    defaultConfig {
        applicationId = "com.filmfoundry.mobile"
        minSdk = 26
        targetSdk = 37
        // Keep a monotonically increasing build code; signature identity also governs upgrades.
        versionCode = 34
        versionName = "0.1.0"
        testInstrumentationRunner = "com.filmfoundry.automation.DeviceTestRunner"
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    signingConfigs {
        if (releaseStore != null) {
            create("distribution") {
                storeFile = file(releaseStore)
                storePassword = releaseStorePassword
                keyAlias = releaseAlias
                keyPassword = releaseKeyPassword
            }
        }
    }
    buildTypes {
        getByName("release") {
            isMinifyEnabled = false
            if (releaseStore != null) signingConfig = signingConfigs.getByName("distribution")
        }
    }
    // Opt in for ABI-specific downloads; keep the default app-debug.apk for device checks.
    splits {
        abi {
            isEnable = providers.gradleProperty("splitApks").orNull == "true"
            reset()
            include("arm64-v8a", "armeabi-v7a", "x86", "x86_64")
            isUniversalApk = true
        }
    }
    testOptions {
        unitTests.isReturnDefaultValues = true
        unitTests.all {
            it.useJUnitPlatform()
        }
    }
    sourceSets.getByName("androidTest").assets.srcDir("src/test/resources")
    sourceSets.getByName("main").assets.srcDir("../../third_party_licenses/android/assets")
}

tasks.register("requireReleaseSigning") {
    doLast {
        check(releaseCredentials.all { !it.isNullOrBlank() }) {
            "Release signing is not configured. Set the four FILM_RELEASE_* environment variables; debug signing is never used for release."
        }
        check(file(releaseStore!!).isFile) { "Release keystore file does not exist." }
    }
}
tasks.matching { it.name == "preReleaseBuild" }.configureEach {
    dependsOn("requireReleaseSigning")
}

tasks.register("exportReleaseDependencyInventory") {
    val destination = layout.buildDirectory.file("reports/release-runtime.txt")
    outputs.file(destination)
    outputs.upToDateWhen { false }
    doLast {
        val coordinates = configurations.getByName("releaseRuntimeClasspath").incoming.resolutionResult.allComponents.mapNotNull {
            val id = it.id as? org.gradle.api.artifacts.component.ModuleComponentIdentifier
            id?.let { "${it.group}:${it.module}:${it.version}" }
        }.sorted()
        destination.get().asFile.apply { parentFile.mkdirs(); writeText(coordinates.joinToString("\n", postfix = "\n")) }
        val recorded = rootProject.file("../third_party_licenses/android/DEPENDENCIES.txt")
        check(recorded.isFile && recorded.readLines().filter { it.isNotBlank() } == coordinates) {
            "Android notices are missing or stale. Refresh tools/collect_runtime_notices.py from app/build/reports/release-runtime.txt."
        }
    }
}
tasks.matching { it.name == "preReleaseBuild" }.configureEach {
    dependsOn("exportReleaseDependencyInventory")
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

dependencies {
    implementation(project(":workflow"))
    implementation(project(":platform"))
    implementation(project(":interchange"))
    implementation(libs.coroutines.core)
    implementation(libs.activity)
    implementation(libs.camerax.view)
    testImplementation(libs.junit.jupiter)
    testRuntimeOnly(libs.junit.platform.launcher)
    androidTestImplementation(project(":kernel"))
}
