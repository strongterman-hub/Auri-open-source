package com.auri.chat

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.core.content.FileProvider
import java.io.File
import java.security.MessageDigest

sealed interface UpdateUiState {
    data object Hidden : UpdateUiState
    data class Available(val info: UpdateInfo) : UpdateUiState
    data class Downloading(
        val info: UpdateInfo,
        val received: Long,
        val total: Long,
    ) : UpdateUiState

    data class Ready(val info: UpdateInfo, val file: File) : UpdateUiState
    data class Error(val info: UpdateInfo?, val message: String) : UpdateUiState
}

class AppUpdateManager(private val context: Context) {
    private val api = UpdateApi()

    fun currentVersionCode(): Int = BuildConfig.VERSION_CODE

    fun check(): UpdateInfo = api.check()

    fun resolveDownloadUrl(info: UpdateInfo): String {
        val url = info.downloadUrl
        if (url.isNullOrBlank()) {
            throw IllegalStateException("下载地址为空")
        }
        return if (url.startsWith("http://") || url.startsWith("https://")) {
            url
        } else {
            ApiConfig.BASE_URL + url
        }
    }

    fun download(info: UpdateInfo, onProgress: (Long, Long) -> Unit): File {
        val dir = File(context.cacheDir, "updates").apply { mkdirs() }
        val file = File(dir, "auri.apk")
        api.download(resolveDownloadUrl(info), file, onProgress)

        if (info.apkSize > 0 && file.length() != info.apkSize) {
            file.delete()
            throw IllegalStateException("安装包大小校验失败")
        }
        if (info.sha256 != null) {
            val actual = sha256(file)
            if (!actual.equals(info.sha256, ignoreCase = true)) {
                file.delete()
                throw IllegalStateException("安装包校验失败")
            }
        }
        return file
    }

    fun canInstall(): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.O ||
            context.packageManager.canRequestPackageInstalls()

    fun openInstallPermissionSettings() {
        val intent = Intent(
            Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
            Uri.parse("package:${context.packageName}"),
        ).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        context.startActivity(intent)
    }

    fun install(file: File) {
        val uri = FileProvider.getUriForFile(
            context,
            "${context.packageName}.fileprovider",
            file,
        )
        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, "application/vnd.android.package-archive")
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        context.startActivity(intent)
    }

    private fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(8192)
            while (true) {
                val read = input.read(buffer)
                if (read < 0) break
                digest.update(buffer, 0, read)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }
}
