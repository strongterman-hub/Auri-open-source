package com.auri.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

object AuriTokens {
    val Background = Color(0xFF0B0D13)
    val Surface = Color(0xFF131722)
    val SurfaceVariant = Color(0xFF191E2D)
    val Outline = Color(0xFF2A3145)
    val Primary = Color(0xFF8B5CF6)
    val Secondary = Color(0xFF2FD1A3)
    val Tertiary = Color(0xFFF5B45F)
    val Error = Color(0xFFFF6B6B)
    val TextPrimary = Color(0xFFF4F7FC)
    val TextSecondary = Color(0xFFA9B4C8)
    val Muted = Color(0xFF6E7A90)

    val AuroraTop = Color(0xFF151126)
    val AuroraBottom = Color(0xFF07141D)

    val HeartRate = Color(0xFFE24A6B)
    val Steps = Color(0xFF4AA3DF)
    val Calories = Color(0xFFF0A34A)
    val SpO2 = Color(0xFF6BCB77)
    val Stress = Color(0xFF9E77ED)

    val SleepDeep = Color(0xFF9E77ED)
    val SleepLight = Color(0xFF4AA3DF)
    val SleepRem = Color(0xFF6BCB77)
    val SleepAwake = Color(0xFFF0A34A)
}

private val AuriColorScheme = darkColorScheme(
    primary = AuriTokens.Primary,
    onPrimary = Color.White,
    secondary = AuriTokens.Secondary,
    onSecondary = Color(0xFF04211B),
    tertiary = AuriTokens.Tertiary,
    onTertiary = Color(0xFF241703),
    error = AuriTokens.Error,
    onError = Color(0xFF2B0808),
    background = AuriTokens.Background,
    onBackground = AuriTokens.TextPrimary,
    surface = AuriTokens.Surface,
    onSurface = AuriTokens.TextPrimary,
    surfaceVariant = AuriTokens.SurfaceVariant,
    onSurfaceVariant = AuriTokens.TextSecondary,
    outline = AuriTokens.Outline,
    outlineVariant = AuriTokens.Outline,
    errorContainer = Color(0xFF4A1D24),
    onErrorContainer = Color(0xFFFFDAD6),
    secondaryContainer = Color(0xFF0C2C25),
    onSecondaryContainer = Color(0xFFA9F1DE),
)

private val AuriTypography = Typography(
    titleLarge = TextStyle(
        fontWeight = FontWeight.SemiBold,
        fontSize = 22.sp,
        lineHeight = 30.sp,
    ),
    titleMedium = TextStyle(
        fontWeight = FontWeight.SemiBold,
        fontSize = 18.sp,
        lineHeight = 25.sp,
    ),
    titleSmall = TextStyle(
        fontWeight = FontWeight.SemiBold,
        fontSize = 15.sp,
        lineHeight = 21.sp,
    ),
    bodyLarge = TextStyle(
        fontWeight = FontWeight.Normal,
        fontSize = 16.sp,
        lineHeight = 24.sp,
    ),
    bodyMedium = TextStyle(
        fontWeight = FontWeight.Normal,
        fontSize = 14.sp,
        lineHeight = 21.sp,
    ),
    bodySmall = TextStyle(
        fontWeight = FontWeight.Normal,
        fontSize = 12.sp,
        lineHeight = 18.sp,
    ),
    labelLarge = TextStyle(
        fontWeight = FontWeight.SemiBold,
        fontSize = 15.sp,
        lineHeight = 20.sp,
    ),
)

@Composable
fun AuriTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = AuriColorScheme,
        typography = AuriTypography,
        content = content,
    )
}

@Composable
fun AuriBackground(
    modifier: Modifier = Modifier,
    content: @Composable BoxScope.() -> Unit,
) {
    Box(
        modifier = modifier
            .fillMaxSize()
            .background(
                Brush.verticalGradient(
                    listOf(
                        AuriTokens.AuroraTop,
                        AuriTokens.Background,
                        AuriTokens.AuroraBottom,
                    ),
                ),
            ),
        content = content,
    )
}
