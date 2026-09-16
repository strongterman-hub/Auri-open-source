package com.auri.chat

import org.json.JSONObject

data class PersonaOption(
    val presetId: String,
    val label: String,
    val description: String,
    val frequencyHint: String,
)

data class CharacterOption(
    val presentation: String,
    val label: String,
    val displayName: String,
    val summary: String,
    val avatarUrl: String = "",
)

data class PersonaSelection(
    val presetId: String,
    val presentation: String,
)

data class PersonaOptions(
    val available: Boolean,
    val userSelectionEnabled: Boolean,
    val portraitAvailable: Boolean,
    val selection: PersonaSelection,
    val presets: List<PersonaOption>,
    val characters: List<CharacterOption>,
)
internal fun parsePersonaOptions(json: JSONObject): PersonaOptions? {
    val selectionJson = json.optJSONObject("selection") ?: return null
    val presetId = selectionJson.optString("preset_id").trim()
    val presentation = selectionJson.optString("presentation").trim()
    if (presetId.isEmpty() || presentation.isEmpty()) return null
    val presetsJson = json.optJSONArray("presets")
    val presets = buildList {
        if (presetsJson != null) {
            for (index in 0 until presetsJson.length()) {
                val item = presetsJson.optJSONObject(index) ?: continue
                val id = item.optString("id").trim()
                if (id.isEmpty()) continue
                add(
                    PersonaOption(
                        presetId = id,
                        label = item.optString("label").ifBlank { id },
                        description = item.optString("description").trim(),
                        frequencyHint = frequencyHintForPreset(
                            item.optString("proactive_frequency_preset")
                        ),
                    )
                )
            }
        }
    }
    val charactersJson = json.optJSONArray("characters")
    val characters = buildList {
        if (charactersJson != null) {
            for (index in 0 until charactersJson.length()) {
                val item = charactersJson.optJSONObject(index) ?: continue
                val value = item.optString("presentation").trim()
                if (value.isEmpty()) continue
                add(
                    CharacterOption(
                        presentation = value,
                        label = item.optString("label").ifBlank { value },
                        displayName = item.optString("display_name").ifBlank { "Auri" },
                        summary = item.optString("summary").trim(),
                        avatarUrl = item.optString("avatar_url").trim().let { value ->
                            if (value.isBlank()) "" else resolveImageUrl(value)
                        },
                    )
                )
            }
        }
    }
    return PersonaOptions(
        available = json.optBoolean("available", false),
        userSelectionEnabled = json.optBoolean("user_selection_enabled", false),
        portraitAvailable = json.optBoolean("portrait_available", false),
        selection = PersonaSelection(
            presetId = presetId,
            presentation = presentation,
        ),
        presets = presets,
        characters = characters,
    )
}

internal fun frequencyHintForPreset(value: String): String = when (value.trim().lowercase()) {
    "quiet" -> "主动消息：安静"
    "normal" -> "主动消息：标准"
    "high" -> "主动消息：多"
    "intensive" -> "主动消息：很多"
    else -> ""
}