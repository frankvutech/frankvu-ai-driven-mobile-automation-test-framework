// The framework's on-device Android runner. This builds ONLY the framework's own instrumentation
// APK; it never builds, depends on or modifies the application under test.
pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "mtf-android-runner"
include(":runner")
