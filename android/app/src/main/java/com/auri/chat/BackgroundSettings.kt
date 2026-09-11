package com.auri.chat

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.os.Build

internal data class BackgroundSettingsProfile(
    val family: String,
    val title: String,
    val subtitle: String,
    val componentCandidates: List<Pair<String, String>>,
)

internal fun backgroundSettingsProfile(
    manufacturer: String,
    brand: String,
): BackgroundSettingsProfile? {
    val deviceName = "$manufacturer $brand".trim().lowercase()
    return when {
        listOf("xiaomi", "redmi", "poco").any(deviceName::contains) ->
            BackgroundSettingsProfile(
                family = "xiaomi",
                title = "自启动与后台运行",
                subtitle = "在小米系统设置中允许 Auri 自启动，并将省电策略设为无限制",
                componentCandidates = listOf(
                    "com.miui.securitycenter" to
                        "com.miui.permcenter.autostart.AutoStartManagementActivity",
                    "com.miui.powerkeeper" to
                        "com.miui.powerkeeper.ui.HiddenAppsConfigActivity",
                ),
            )
        listOf("oppo", "oneplus", "realme", "oplus").any(deviceName::contains) ->
            BackgroundSettingsProfile(
                family = "oppo",
                title = "后台运行与耗电管理",
                subtitle = "在 OPPO/一加/realme 系统设置中允许 Auri 后台运行",
                componentCandidates = listOf(
                    "com.oplus.safecenter" to
                        "com.oplus.safecenter.startupapp.StartupAppListActivity",
                    "com.coloros.safecenter" to
                        "com.coloros.safecenter.startupapp.StartupAppListActivity",
                    "com.coloros.safecenter" to
                        "com.coloros.safecenter.permission.startup.StartupAppListActivity",
                ),
            )
        listOf("vivo", "iqoo").any(deviceName::contains) ->
            BackgroundSettingsProfile(
                family = "vivo",
                title = "后台高耗电与自启动",
                subtitle = "在 vivo/iQOO 系统设置中允许 Auri 后台活动",
                componentCandidates = listOf(
                    "com.vivo.permissionmanager" to
                        "com.vivo.permissionmanager.activity.BgStartUpManagerActivity",
                    "com.vivo.permissionmanager" to
                        "com.vivo.permissionmanager.activity.PurviewTabActivity",
                ),
            )
        deviceName.contains("huawei") ->
            BackgroundSettingsProfile(
                family = "huawei",
                title = "应用启动管理",
                subtitle = "在华为系统设置中允许 Auri 自动启动和后台运行",
                componentCandidates = listOf(
                    "com.huawei.systemmanager" to
                        "com.huawei.systemmanager.startupmgr.ui.StartupNormalAppListActivity",
                ),
            )
        deviceName.contains("honor") ->
            BackgroundSettingsProfile(
                family = "honor",
                title = "应用启动管理",
                subtitle = "在荣耀系统设置中允许 Auri 自动启动和后台运行",
                componentCandidates = listOf(
                    "com.hihonor.systemmanager" to
                        "com.huawei.systemmanager.startupmgr.ui.StartupNormalAppListActivity",
                    "com.hihonor.systemmanager" to
                        "com.hihonor.systemmanager.startupmgr.ui.StartupNormalAppListActivity",
                ),
            )
        deviceName.contains("samsung") ->
            BackgroundSettingsProfile(
                family = "samsung",
                title = "电池与后台使用",
                subtitle = "请在应用详情中允许 Auri 后台活动，并避免加入深度休眠",
                componentCandidates = emptyList(),
            )
        deviceName.contains("meizu") ->
            BackgroundSettingsProfile(
                family = "meizu",
                title = "后台运行与自启动",
                subtitle = "在魅族系统设置中允许 Auri 后台运行",
                componentCandidates = listOf(
                    "com.meizu.safe" to "com.meizu.safe.permission.SmartBGActivity",
                ),
            )
        else -> null
    }
}

internal fun currentBackgroundSettingsProfile(): BackgroundSettingsProfile? =
    backgroundSettingsProfile(Build.MANUFACTURER.orEmpty(), Build.BRAND.orEmpty())

internal fun backgroundSettingsIntent(
    context: Context,
    profile: BackgroundSettingsProfile,
): Intent {
    profile.componentCandidates.forEach { (packageName, className) ->
        val intent = Intent().setComponent(ComponentName(packageName, className))
        if (intent.resolveActivity(context.packageManager) != null) return intent
    }
    return openAppDetails(context)
}

internal fun openBackgroundSettings(
    context: Context,
    profile: BackgroundSettingsProfile,
) {
    val target = backgroundSettingsIntent(context, profile)
    runCatching { context.startActivity(target) }
        .onFailure { context.startActivity(openAppDetails(context)) }
}
