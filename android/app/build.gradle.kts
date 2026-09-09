import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.google.devtools.ksp")
}

val apiBaseUrl: String = (project.findProperty("apiBaseUrl") as String?)
    ?: "https://your-domain.example/v1"

val jpushAppKey = (project.findProperty("jpushAppKey") as String?).orEmpty()
val keystoreProperties = Properties()
rootProject.file("keystore.properties").takeIf { it.exists() }?.inputStream()?.use {
    keystoreProperties.load(it)
}

android {
    namespace = "com.auri.chat"
    compileSdk = 35

    defaultConfig {
        val appId = (project.findProperty("applicationId") as String?) ?: "com.auri.community"
        applicationId = appId
        minSdk = 26
        targetSdk = 35
        versionCode = 32
        versionName = "0.3.29"
        buildConfigField("String", "API_BASE_URL", "\"$apiBaseUrl\"")

        manifestPlaceholders["JPUSH_PKGNAME"] = appId
        manifestPlaceholders["JPUSH_APPKEY"] = jpushAppKey.ifBlank { "000000000000000000000000" }
        buildConfigField("boolean", "JPUSH_ENABLED", jpushAppKey.isNotBlank().toString())
        manifestPlaceholders["JPUSH_CHANNEL"] = "developer-default"
    }

    flavorDimensions += "distribution"
    productFlavors {
        create("store") {
            dimension = "distribution"
            buildConfigField("boolean", "SELF_UPDATE_ENABLED", "false")
            buildConfigField("boolean", "KEEP_ALIVE_ENABLED", "false")
        }
        create("direct") {
            dimension = "distribution"
            buildConfigField("boolean", "SELF_UPDATE_ENABLED", "true")
            buildConfigField("boolean", "KEEP_ALIVE_ENABLED", "true")
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    signingConfigs {
        create("release") {
            storeFile = rootProject.file(
                keystoreProperties.getProperty("storeFile") ?: "auri-release.keystore",
            )
            storePassword = keystoreProperties.getProperty("storePassword") ?: ""
            keyAlias = keystoreProperties.getProperty("keyAlias") ?: "auri"
            keyPassword = keystoreProperties.getProperty("keyPassword") ?: ""
        }
    }

    buildTypes {
        getByName("debug") {
            applicationIdSuffix = (project.findProperty("debugApplicationIdSuffix") as String?) ?: ".debug"
            val debugUrl = (project.findProperty("apiBaseUrl") as String?) ?: "http://10.0.2.2:8010/v1"
            buildConfigField("String", "API_BASE_URL", "\"$debugUrl\"")
            buildConfigField("boolean", "ALIPAY_SANDBOX", "true")
        }
        release {
            isMinifyEnabled = false
            if (rootProject.file("keystore.properties").exists()) {
                signingConfig = signingConfigs.getByName("release")
            }
            buildConfigField("boolean", "ALIPAY_SANDBOX", "false")
        }
    }

    composeOptions {
        kotlinCompilerExtensionVersion = "1.5.10"
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-compose:1.9.0")
    implementation(platform("androidx.compose:compose-bom:2024.06.00"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-core")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.0")
    implementation("androidx.room:room-runtime:2.6.1")
    implementation("androidx.room:room-ktx:2.6.1")
    ksp("androidx.room:room-compiler:2.6.1")
    implementation("io.noties.markwon:core:4.6.2")
    implementation("io.noties.markwon:ext-tables:4.6.2")
    implementation("io.noties.markwon:ext-strikethrough:4.6.2")
    implementation("io.noties.markwon:ext-tasklist:4.6.2")
    implementation("cn.jiguang.sdk:jpush:5.8.0")
    implementation("com.alipay.sdk:alipaysdk-android:15.8.42")
    debugImplementation("androidx.compose.ui:ui-tooling")
    testImplementation("junit:junit:4.13.2")
}
