// A self-instrumenting APK: the instrumentation targets this APK's own package and drives the
// application under test as a black box through UIAutomator (accessibility layer). Because it does
// not instrument the app's process, the app can be any prebuilt APK, signed with any key.
plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jlleitschuh.gradle.ktlint")
}

android {
    namespace = "dev.mtf.androidrunner"
    compileSdk = 35

    defaultConfig {
        applicationId = "dev.mtf.androidrunner"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"
    }

    buildTypes {
        debug { isMinifyEnabled = false }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    lint {
        warningsAsErrors = true
        abortOnError = true
        // The runner is a test tool installed only by the framework, never published or backed up.
        disable +=
            setOf(
                "MissingApplicationIcon",
                "DataExtractionRules",
                "ExpiredTargetSdkVersion",
                "OldTargetApi",
                "GradleDependency",
                "AndroidGradlePluginVersion",
                "NewerVersionAvailable",
            )
    }
}

kotlin {
    compilerOptions { jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17) }
}

ktlint {
    version.set("1.7.1")
    android.set(true)
}

dependencies {
    implementation("androidx.test.uiautomator:uiautomator:2.3.0")
    implementation("androidx.test:monitor:1.7.2")
    testImplementation("junit:junit:4.13.2")
}
