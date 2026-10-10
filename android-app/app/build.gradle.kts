import java.net.URI
import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Set the actual public HTTPS origin through -PacademyBaseUrl=... or the ignored
// android-app/local.properties entry academyBaseUrl=https://your-real-domain.example.
val localBuildProperties = Properties().apply {
    val localFile = rootProject.file("local.properties")
    if (localFile.isFile) localFile.inputStream().use { load(it) }
}
val localAcademyBaseUrl = localBuildProperties.getProperty("academyBaseUrl")
val academyBaseUrl = providers.gradleProperty("academyBaseUrl")
    .orElse(localAcademyBaseUrl ?: "https://replace-with-your-deployed-domain.invalid")
    .get()
    .trim()
    .trimEnd('/')

val parsedBase = URI(academyBaseUrl)
if (parsedBase.scheme != "https" || parsedBase.host.isNullOrBlank() ||
    parsedBase.rawUserInfo != null || parsedBase.rawQuery != null || parsedBase.rawFragment != null ||
    (parsedBase.rawPath != null && parsedBase.rawPath.isNotEmpty() && parsedBase.rawPath != "/")) {
    throw GradleException("academyBaseUrl must be an HTTPS origin only, e.g. https://academy.example.com")
}

val escapedBaseUrl = academyBaseUrl.replace("\\", "\\\\").replace("\"", "\\\"")

android {
    namespace = "com.infiniteacademy.app"
    compileSdk = 35
    buildToolsVersion = "35.0.0"

    defaultConfig {
        applicationId = "com.infiniteacademy.app"
        minSdk = 23
        targetSdk = 35
        versionCode = 1
        versionName = "1.0.0"
        buildConfigField("String", "ACADEMY_BASE_URL", "\"$escapedBaseUrl\"")
    }
    buildFeatures { buildConfig = true }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    implementation("androidx.webkit:webkit:1.12.1")
}
