package com.auri.chat

import android.Manifest
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.widget.Toast
import android.provider.Settings
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.automirrored.outlined.KeyboardArrowRight
import androidx.compose.material.icons.outlined.LocationOn
import androidx.compose.material.icons.outlined.Lock
import androidx.compose.material.icons.outlined.Notifications
import androidx.compose.material.icons.outlined.AddCircle
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material3.AlertDialog
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AccountCenterScreen(
    email: String,
    onBack: () -> Unit,
    onOpenCredits: () -> Unit,
    onOpenChangePassword: () -> Unit,
    onLogout: () -> Unit,
    onDeleteAccount: () -> Unit,
    versionState: AppVersionState = AppVersionState(),
    onRefreshVersion: () -> Unit = {},
    onCheckUpdate: () -> Unit = {},
    loggingOut: Boolean = false,
) {
    val context = LocalContext.current
    val prefs = remember {
        context.getSharedPreferences("auri_settings", Context.MODE_PRIVATE)
    }
    val authStore = remember { AuthStore(context) }
    val scope = rememberCoroutineScope()
    var proactiveEnabled by remember {
        mutableStateOf(prefs.getBoolean("proactive_enabled", false))
    }
    var notificationsGranted by remember { mutableStateOf(areNotificationsGranted(context)) }
    var locationGranted by remember { mutableStateOf(isLocationGranted(context)) }
    var deleteConfirm by remember { mutableStateOf(false) }
    var showPrivacyPolicy by remember { mutableStateOf(false) }
    var deleting by remember { mutableStateOf(false) }
    var deleteError by remember { mutableStateOf<String?>(null) }
    var creditsBalance by remember { mutableStateOf<String?>(null) }

    if (showPrivacyPolicy) {
        PrivacyPolicyDialog(onDismiss = { showPrivacyPolicy = false })
    }

    LaunchedEffect(Unit) {
        onRefreshVersion()
    }

    val notificationPermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        notificationsGranted = granted
    }
    val locationPermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        locationGranted = granted
    }

    LaunchedEffect(Unit) {
        val token = authStore.getToken() ?: return@LaunchedEffect
        val enabled = withContext(Dispatchers.IO) {
            runCatching {
                AuriApi().getProactiveSettings(token).optBoolean("enabled", false)
            }.getOrDefault(false)
        }
        proactiveEnabled = enabled
        prefs.edit().putBoolean("proactive_enabled", enabled).apply()
    }

    LaunchedEffect(Unit) {
        val token = authStore.getToken() ?: return@LaunchedEffect
        creditsBalance = withContext(Dispatchers.IO) {
            runCatching { CreditsApi().balance(token).balanceCredits }.getOrNull()
        }
    }

    BackHandler(onBack = onBack)

    AuriBackground {
        Scaffold(
            modifier = Modifier.fillMaxSize(),
            containerColor = Color.Transparent,
            topBar = {
                TopAppBar(
                    navigationIcon = {
                        TextButton(onClick = onBack) {
                            Icon(
                                imageVector = Icons.AutoMirrored.Outlined.ArrowBack,
                                contentDescription = "返回",
                                tint = MaterialTheme.colorScheme.onSurface,
                            )
                        }
                    },
                    title = {
                        Text(
                            "账号中心",
                            color = MaterialTheme.colorScheme.onSurface,
                        )
                    },
                    colors = TopAppBarDefaults.topAppBarColors(
                        containerColor = MaterialTheme.colorScheme.surface.copy(alpha = 0.62f),
                        titleContentColor = MaterialTheme.colorScheme.onSurface,
                    ),
                )
            },
        ) { innerPadding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding)
                .verticalScroll(rememberScrollState())
                .padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Spacer(modifier = Modifier.height(24.dp))
            Surface(
                modifier = Modifier.size(88.dp),
                shape = CircleShape,
                color = MaterialTheme.colorScheme.primary,
            ) {
                Box(contentAlignment = Alignment.Center) {
                    Text(
                        text = email.firstOrNull()?.uppercase() ?: "A",
                        color = MaterialTheme.colorScheme.onPrimary,
                        style = MaterialTheme.typography.titleLarge,
                        fontWeight = FontWeight.Bold,
                        textAlign = TextAlign.Center,
                    )
                }
            }
            Spacer(modifier = Modifier.height(16.dp))
            Text(
                text = email.ifBlank { "Auri 用户" },
                color = MaterialTheme.colorScheme.onSurface,
                style = MaterialTheme.typography.titleMedium,
                textAlign = TextAlign.Center,
            )
            Spacer(modifier = Modifier.height(28.dp))
            Surface(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(14.dp),
                color = MaterialTheme.colorScheme.surfaceVariant,
            ) {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp, vertical = 8.dp),
                ) {
                    Text(
                        text = "账号安全",
                        style = MaterialTheme.typography.titleSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(vertical = 8.dp),
                    )
                    PermissionActionRow(
                        icon = Icons.Outlined.Lock,
                        title = "修改密码",
                        subtitle = "通过当前邮箱验证码确认身份",
                        onClick = onOpenChangePassword,
                    )
                }
            }
            Spacer(modifier = Modifier.height(16.dp))
            Surface(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(14.dp),
                color = MaterialTheme.colorScheme.surfaceVariant,
            ) {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp, vertical = 8.dp),
                ) {
                    Text(
                        text = "Credits",
                        style = MaterialTheme.typography.titleSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(vertical = 8.dp),
                    )
                    PermissionActionRow(
                        icon = Icons.Outlined.AddCircle,
                        title = "Credits 余额",
                        subtitle = creditsBalance?.let { "${formatCredits(it)} Credits" }
                            ?: "查看余额与充值",
                        onClick = onOpenCredits,
                    )
                }
            }
            Spacer(modifier = Modifier.height(16.dp))
            Surface(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(14.dp),
                color = MaterialTheme.colorScheme.surfaceVariant,
            ) {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp, vertical = 8.dp),
                ) {
                    Text(
                        text = "消息与权限",
                        style = MaterialTheme.typography.titleSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(vertical = 8.dp),
                    )
                    PermissionSwitchRow(
                        icon = Icons.Outlined.Notifications,
                        title = "主动消息",
                        subtitle = "允许 Auri 在合适的时机主动联系你；会消耗 Credits",
                        checked = proactiveEnabled,
                        onCheckedChange = {
                            proactiveEnabled = it
                            prefs.edit().putBoolean("proactive_enabled", it).apply()
                            if (
                                BuildConfig.KEEP_ALIVE_ENABLED &&
                                it &&
                                areNotificationsGranted(context)
                            ) {
                                AuriKeepAliveController.start(context)
                            }
                            val token = authStore.getToken()
                            if (token != null) {
                                scope.launch {
                                    withContext(Dispatchers.IO) {
                                        runCatching {
                                            AuriApi().updateProactiveSettings(it, token)
                                        }
                                    }
                                }
                            }
                        },
                    )
                    PermissionActionRow(
                        icon = Icons.Outlined.Notifications,
                        title = "推送通知",
                        subtitle = if (notificationsGranted) "已开启" else "未开启",
                        onClick = {
                            if (Build.VERSION.SDK_INT >= 33 && !notificationsGranted) {
                                notificationPermissionLauncher.launch(
                                    Manifest.permission.POST_NOTIFICATIONS,
                                )
                            } else {
                                context.startActivity(openNotificationSettings(context))
                            }
                        },
                    )
                    PermissionActionRow(
                        icon = Icons.Outlined.LocationOn,
                        title = "位置权限（GPS）",
                        subtitle = if (locationGranted) "已开启" else "未开启",
                        onClick = {
                            if (!locationGranted) {
                                locationPermissionLauncher.launch(
                                    Manifest.permission.ACCESS_FINE_LOCATION,
                                )
                            } else {
                                context.startActivity(openAppDetails(context))
                            }
                        },
                    )
                    if (BuildConfig.KEEP_ALIVE_ENABLED) {
                        PermissionActionRow(
                            icon = Icons.Outlined.Settings,
                            title = "自启动权限",
                            subtitle = "用于后台接收主动消息",
                            onClick = {
                                context.startActivity(autostartIntent(context))
                            },
                        )
                    }
                }
            }
            Spacer(modifier = Modifier.height(16.dp))
            Surface(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(14.dp),
                color = MaterialTheme.colorScheme.surfaceVariant,
            ) {
                PermissionActionRow(
                    icon = Icons.Outlined.Info,
                    title = "APP 版本",
                    subtitle = buildString {
                        append("v${BuildConfig.VERSION_NAME}")
                        append(" · ")
                        append(when {
                            !BuildConfig.SELF_UPDATE_ENABLED -> "通过应用商店更新"
                            versionState.checking -> "正在检查…"
                            versionState.failed -> "检查失败，点击重试"
                            versionState.available != null -> "最新 v${versionState.available.versionName}"
                            versionState.checked -> "已是最新版本"
                            else -> "点击检查更新"
                        })
                    },
                    badge = if (BuildConfig.SELF_UPDATE_ENABLED && versionState.available != null) {
                        "有新版本"
                    } else null,
                    onClick = {
                        if (BuildConfig.SELF_UPDATE_ENABLED) {
                            onCheckUpdate()
                        } else {
                            Toast.makeText(context, "请通过应用商店检查更新", Toast.LENGTH_SHORT).show()
                        }
                    },
                )
            }
            Spacer(modifier = Modifier.height(24.dp))
            OutlinedButton(
                onClick = { showPrivacyPolicy = true },
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(14.dp),
            ) { Text("隐私政策与第三方服务") }
            Spacer(modifier = Modifier.height(8.dp))
            OutlinedButton(
                onClick = onLogout,
                enabled = !loggingOut,
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(14.dp),
            ) {
                Text(if (loggingOut) "正在退出…" else "退出登录")
            }
            Spacer(modifier = Modifier.height(8.dp))
            TextButton(
                onClick = { deleteConfirm = true },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text(
                    "删除账号",
                    color = MaterialTheme.colorScheme.error,
                )
            }
            deleteError?.let {
                Spacer(modifier = Modifier.height(8.dp))
                Text(
                    text = it,
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                    textAlign = TextAlign.Center,
                )
            }
        }
    }
    }

    if (deleteConfirm) {
        AlertDialog(
            onDismissRequest = { if (!deleting) deleteConfirm = false },
            title = { Text("删除账号？") },
            text = {
                Text("删除后该账号及所有聊天、记忆、健康数据都会被永久清除，相当于重新开始，且无法恢复。")
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        if (deleting) return@TextButton
                        deleting = true
                        deleteError = null
                        val token = authStore.getToken()
                        if (token == null) {
                            deleting = false
                            deleteError = "登录状态已失效，请重新登录后再试"
                            return@TextButton
                        }
                        scope.launch {
                            val result = withContext(Dispatchers.IO) {
                                runCatching { AuthApi().deleteAccount(token) }
                            }
                            if (result.isSuccess) {
                                deleteConfirm = false
                                deleting = false
                                onDeleteAccount()
                            } else {
                                deleting = false
                                deleteError = result.exceptionOrNull()?.message ?: "删除失败"
                            }
                        }
                    },
                ) {
                    Text(
                        if (deleting) "删除中…" else "确认删除",
                        color = MaterialTheme.colorScheme.error,
                    )
                }
            },
            dismissButton = {
                TextButton(
                    onClick = { deleteConfirm = false },
                    enabled = !deleting,
                ) {
                    Text("取消")
                }
            },
        )
    }
}

@Composable
private fun PermissionSwitchRow(
    icon: ImageVector,
    title: String,
    subtitle: String,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(
            imageVector = icon,
            contentDescription = null,
            tint = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.size(22.dp),
        )
        Spacer(modifier = Modifier.width(12.dp))
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = title,
                style = MaterialTheme.typography.bodyLarge,
                color = MaterialTheme.colorScheme.onSurface,
            )
            Text(
                text = subtitle,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Switch(checked = checked, onCheckedChange = onCheckedChange)
    }
}

@Composable
private fun PermissionActionRow(
    icon: ImageVector,
    title: String,
    subtitle: String,
    onClick: () -> Unit,
    badge: String? = null,
) {
    TextButton(
        onClick = onClick,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Icon(
            imageVector = icon,
            contentDescription = null,
            tint = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.size(22.dp),
        )
        Spacer(modifier = Modifier.width(12.dp))
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = title,
                style = MaterialTheme.typography.bodyLarge,
                color = MaterialTheme.colorScheme.onSurface,
            )
            Text(
                text = subtitle,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        if (badge != null) {
            Surface(
                shape = RoundedCornerShape(8.dp),
                color = MaterialTheme.colorScheme.primaryContainer,
            ) {
                Text(
                    text = badge,
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onPrimaryContainer,
                    modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                )
            }
            Spacer(modifier = Modifier.width(4.dp))
        }
        Icon(
            imageVector = Icons.AutoMirrored.Outlined.KeyboardArrowRight,
            contentDescription = null,
            tint = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

internal fun areNotificationsGranted(context: Context): Boolean =
    Build.VERSION.SDK_INT < 33 ||
        ContextCompat.checkSelfPermission(
            context,
            Manifest.permission.POST_NOTIFICATIONS,
        ) == PackageManager.PERMISSION_GRANTED

internal fun isLocationGranted(context: Context): Boolean =
    ContextCompat.checkSelfPermission(
        context,
        Manifest.permission.ACCESS_FINE_LOCATION,
    ) == PackageManager.PERMISSION_GRANTED ||
        ContextCompat.checkSelfPermission(
            context,
            Manifest.permission.ACCESS_COARSE_LOCATION,
        ) == PackageManager.PERMISSION_GRANTED

internal fun openAppDetails(context: Context): Intent =
    Intent(
        Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
        Uri.fromParts("package", context.packageName, null),
    )

internal fun openNotificationSettings(context: Context): Intent =
    Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
        .putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName)

internal fun autostartIntent(context: Context): Intent {
    val candidates = listOf(
        ComponentName(
            "com.miui.securitycenter",
            "com.miui.permcenter.autostart.AutoStartManagementActivity",
        ),
        ComponentName(
            "com.huawei.systemmanager",
            "com.huawei.systemmanager.startupmgr.ui.StartupNormalAppListActivity",
        ),
        ComponentName(
            "com.coloros.safecenter",
            "com.coloros.safecenter.startupapp.StartupAppListActivity",
        ),
        ComponentName(
            "com.vivo.permissionmanager",
            "com.vivo.permissionmanager.activity.BgStartUpManagerActivity",
        ),
    )
    for (component in candidates) {
        val intent = Intent().setComponent(component)
        if (intent.resolveActivity(context.packageManager) != null) {
            return intent
        }
    }
    return openAppDetails(context)
}
