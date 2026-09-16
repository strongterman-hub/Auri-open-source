package com.auri.chat

import org.junit.Assert.assertEquals
import org.junit.Test

class PersonaSettingsTest {
    @Test
    fun frequencyHintMapping() {
        assertEquals("主动消息：安静", frequencyHintForPreset("quiet"))
        assertEquals("主动消息：标准", frequencyHintForPreset("normal"))
        assertEquals("主动消息：多", frequencyHintForPreset("high"))
        assertEquals("主动消息：很多", frequencyHintForPreset("intensive"))
        assertEquals("", frequencyHintForPreset("unknown"))
    }

    @Test
    fun personaSelectionCarriesValues() {
        val selection = PersonaSelection(presetId = "calm", presentation = "male")
        assertEquals("calm", selection.presetId)
        assertEquals("male", selection.presentation)
    }

    @Test
    fun characterOptionCarriesAvatarUrl() {
        val character = CharacterOption(
            presentation = "male",
            label = "男",
            displayName = "Auri",
            summary = "",
            avatarUrl = "/static/portrait/avatar/male.jpg",
        )
        assertEquals("/static/portrait/avatar/male.jpg", character.avatarUrl)
    }
}
