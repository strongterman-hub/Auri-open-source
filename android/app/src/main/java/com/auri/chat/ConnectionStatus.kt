package com.auri.chat

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities

internal fun hasNetworkConnection(context: Context): Boolean {
    val manager = context.getSystemService(ConnectivityManager::class.java) ?: return false
    val network = manager.activeNetwork ?: return false
    return manager.getNetworkCapabilities(network)
        ?.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) == true
}
