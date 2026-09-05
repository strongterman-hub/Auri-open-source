package com.auri.chat

import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.delay

/** Synthetic update responses for visual QA; never downloads or installs. */
class UpdateCaptureActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val scenario = intent.getStringExtra("scenario") ?: "available"
        var dialog by mutableStateOf<UpdateUiState>(UpdateUiState.Hidden)
        val checker = AppUpdateChecker(BuildConfig.VERSION_CODE, lifecycleScope, {
            delay(600)
            if (scenario == "error") error("offline fixture")
            UpdateInfo(
                BuildConfig.VERSION_CODE + if (scenario == "available") 1 else 0,
                if (scenario == "available") "0.3.25" else BuildConfig.VERSION_NAME,
                false, null, null, 0, "优化使用体验并修复已知问题。",
            )
        }) { info, manual, prompt ->
            if (info != null && info.versionCode > BuildConfig.VERSION_CODE && prompt) {
                dialog = UpdateUiState.Available(info)
            } else if (manual) {
                Toast.makeText(this, if (info == null) "检查更新失败，请稍后重试" else "已是最新版本", Toast.LENGTH_SHORT).show()
            }
        }
        setContent {
            val state by checker.state.collectAsState()
            AuriTheme {
                AccountCenterScreen(
                    email = "version-preview@example.com",
                    onBack = { finish() }, onOpenCredits = {}, onOpenChangePassword = {},
                    onLogout = {}, onDeleteAccount = {},
                    versionState = state,
                    onRefreshVersion = { checker.check(prompt = false) },
                    onCheckUpdate = { checker.check(manual = true) },
                )
                UpdateDialog(
                    state = dialog,
                    onDownload = {}, onInstall = {},
                    onDismiss = { dialog = UpdateUiState.Hidden },
                    onRetry = { checker.check(manual = true) },
                )
            }
        }
    }
}
