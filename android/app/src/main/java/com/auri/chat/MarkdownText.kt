package com.auri.chat

import android.content.Context
import android.widget.TextView
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.viewinterop.AndroidView
import io.noties.markwon.AbstractMarkwonPlugin
import io.noties.markwon.Markwon
import io.noties.markwon.core.MarkwonTheme
import io.noties.markwon.ext.strikethrough.StrikethroughPlugin
import io.noties.markwon.ext.tables.TablePlugin
import io.noties.markwon.ext.tables.TableTheme
import io.noties.markwon.ext.tasklist.TaskListPlugin

@Composable
fun MarkdownText(
    text: String,
    modifier: Modifier = Modifier,
    color: Color = MaterialTheme.colorScheme.onSurface,
) {
    val context = LocalContext.current
    val markwon = markwonFor(context)

    AndroidView(
        modifier = modifier,
        factory = { viewContext ->
            TextView(viewContext).apply {
                includeFontPadding = false
                textSize = 16f
                setTextIsSelectable(true)
            }
        },
        update = { textView ->
            textView.setTextColor(color.toArgb())
            markwon.setMarkdown(textView, normalizeMarkdown(text))
            textView.setTextIsSelectable(true)
        },
    )
}

@Volatile
private var cachedMarkwon: Markwon? = null

private val markwonLock = Any()

private fun markwonFor(context: Context): Markwon {
    val appContext = context.applicationContext
    return cachedMarkwon ?: synchronized(markwonLock) {
        cachedMarkwon ?: buildMarkwon(appContext).also { cachedMarkwon = it }
    }
}

private fun buildMarkwon(context: Context): Markwon {
    return Markwon.builder(context)
        .usePlugin(
            object : AbstractMarkwonPlugin() {
                override fun configureTheme(builder: MarkwonTheme.Builder) {
                    builder
                        .linkColor(AuriTokens.Primary.toArgb())
                        .blockQuoteColor(AuriTokens.Muted.toArgb())
                        .listItemColor(AuriTokens.TextSecondary.toArgb())
                        .codeTextColor(AuriTokens.TextPrimary.toArgb())
                        .codeBackgroundColor(0x1AFFFFFF)
                        .codeBlockTextColor(AuriTokens.TextPrimary.toArgb())
                        .codeBlockBackgroundColor(AuriTokens.Background.toArgb())
                        .headingBreakColor(AuriTokens.Outline.toArgb())
                        .thematicBreakColor(AuriTokens.Outline.toArgb())
                }
            },
        )
        .usePlugin(
            TablePlugin.create(
                object : TablePlugin.ThemeConfigure {
                    override fun configureTheme(builder: TableTheme.Builder) {
                        builder
                            .tableBorderColor(AuriTokens.Outline.toArgb())
                            .tableOddRowBackgroundColor(0x14FFFFFF)
                            .tableHeaderRowBackgroundColor(0x24FFFFFF)
                    }
                },
            ),
        )
        .usePlugin(StrikethroughPlugin.create())
        .usePlugin(TaskListPlugin.create(context))
        .build()
}

/**
 * Markwon 4.6.2 bundles commonmark 0.13.0, whose GFM table parser cannot
 * interrupt a paragraph. The model often emits a bold heading immediately
 * followed by a table with only a single newline between them, for example:
 *
 *     **📊 你的 BMI 记录**
 *     | 日期 | BMI |
 *     |------|------|
 *
 * In that case commonmark treats the table rows as ordinary paragraph text and
 * Markwon renders the raw pipes. Insert a blank line before the table header so
 * the parser recognises it as a table block.
 *
 * The model also sometimes emits bold with stray spaces inside the markers, such
 * as `** 加粗内容 **`, `** 加粗内容**` or `**加粗内容 **`. Commonmark does not
 * treat these as strong emphasis, so the raw asterisks are shown. Collapse the
 * horizontal whitespace just inside each `**...**` pair before rendering.
 */
private val BOLD_SPACES = Regex("""\*\*[ \t]*(.+?)[ \t]*\*\*""")

private fun normalizeMarkdown(raw: String): String {
    val text = collapseBoldSpaces(raw)
    if (!text.contains('|')) return text

    val lines = text.split('\n')
    val out = ArrayList<String>(lines.size + 4)
    for (i in lines.indices) {
        val line = lines[i]
        val prev = out.lastOrNull()?.trimEnd('\r')
        val next = lines.getOrNull(i + 1)?.trimEnd('\r')
        val startsTable = line.trimEnd('\r').isTableHeader() && next?.isTableDelimiter() == true
        if (startsTable && !prev.isNullOrBlank() && !prev.isTableLine()) {
            out.add("")
        }
        out.add(line)
    }
    return out.joinToString("\n")
}

private fun collapseBoldSpaces(raw: String): String =
    BOLD_SPACES.replace(raw) { "**${it.groupValues[1]}**" }

private fun String.isTableHeader(): Boolean {
    val t = trim()
    return t.startsWith("|") && t.endsWith("|") && t.length > 1
}

private fun String.isTableDelimiter(): Boolean {
    val t = trim()
    if (!t.startsWith("|") || !t.endsWith("|") || t.length <= 2) return false
    val body = t.removePrefix("|").removeSuffix("|")
    return body.split('|').all { cell ->
        cell.isNotBlank() && cell.all { it == '-' || it == ':' || it == ' ' }
    }
}

private fun String.isTableLine(): Boolean = isTableHeader() || isTableDelimiter()
