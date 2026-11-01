pluginManagement {
    repositories {
        google()
        maven { url = uri("https://maven.aliyun.com/repository/google") }
        mavenCentral()
        maven { url = uri("https://maven.aliyun.com/repository/central") }
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        maven { url = uri("https://maven.aliyun.com/repository/google") }
        mavenCentral()
        maven { url = uri("https://maven.aliyun.com/repository/central") }
    }
}

rootProject.name = "film-foundry-mobile"

include(
    ":interchange",
    ":kernel",
    ":workflow",
    ":platform",
    ":app",
)
