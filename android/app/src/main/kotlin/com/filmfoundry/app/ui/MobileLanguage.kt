package com.filmfoundry.app.ui

import android.content.Context
import android.content.res.AssetManager
import android.content.res.Configuration
import android.util.Log
import org.json.JSONObject
import java.util.Locale

/** Frontend copy only. A port may bundle desktop v1 dictionaries in assets/languages. */
internal class MobileLanguage(
    val chineseFallback: Boolean,
    private val translations: Map<String, String> = emptyMap()
) {
    fun text(zh: String, en: String): String {
        val fallback = if (chineseFallback) zh else en
        if (translations.isEmpty()) return fallback
        if (zh == en) return en
        // Older presenters use this pair to choose a built-in formatter.
        if (zh == "zh" && en == "en") return fallback
        return translations["text:" + en] ?: fallback
    }

    fun message(id: String, zh: String, en: String, vararg fields: Pair<String, String>): String {
        val fallback = if (chineseFallback) zh else en
        val values = fields.toMap()
        val translated = translations["key:" + id]
        val pattern = translated?.takeIf { names(it) == names(en) } ?: fallback
        return placeholder.replace(pattern) { values[it.groupValues[1]] ?: it.value }
    }

    companion object {
        private val placeholder = Regex("\\{([a-zA-Z_][a-zA-Z0-9_]*)\\}")
        private fun names(pattern: String) = placeholder.findAll(pattern).map { it.groupValues[1] }.sorted().toList()
    }
}

internal object MobileLanguages {
    private data class Pack(val name: String, val language: MobileLanguage)
    private val codePattern = Regex("[a-z]{2,3}(?:_[A-Z][a-z]{3})?(?:_(?:[A-Z]{2}|[0-9]{3}))?")
    private val reserved = setOf("zh", "en", "zh_CN", "en_US")
    private val chinese = MobileLanguage(true)
    private val english = MobileLanguage(false)
    @Volatile private var cached: Map<String, Pack>? = null

    private fun bundled(context: Context): Map<String, Pack> =
        cached ?: synchronized(this) {
            cached ?: read(context.assets).also { cached = it }
        }

    private fun read(assets: AssetManager): Map<String, Pack> = buildMap {
        for (file in assets.list("languages").orEmpty().sorted()) {
            if (!file.endsWith(".json")) continue
            val code = file.removeSuffix(".json")
            if (!codePattern.matches(code) || code in reserved) continue
            try {
                val data = assets.open("languages/" + file).bufferedReader(Charsets.UTF_8).use { JSONObject(it.readText()) }
                require(data.getString("format") == "film-foundry-language" && data.getInt("version") == 1)
                require(data.getString("code") == code)
                val fallback = data.getString("fallback")
                require(fallback == "en_US" || fallback == "zh_CN")
                val name = data.getString("name")
                require(name.isNotBlank())
                val entries = data.getJSONObject("translations")
                val translations = buildMap {
                    for (key in entries.keys()) {
                        val value = entries.get(key)
                        require(value is String)
                        if (value.isNotBlank()) put(key, value)
                    }
                }
                put(code, Pack(name, MobileLanguage(fallback == "zh_CN", translations)))
            } catch (error: Exception) {
                Log.w("MobileLanguages", "Ignoring invalid bundled language " + file, error)
            }
        }
    }

    fun choices(context: Context): List<Pair<String,String>> = bundled(context).map { it.key to it.value.name }

    fun forContext(context: Context): MobileLanguage {
        val locale = context.resources.configuration.locales[0]
        val packs = bundled(context)
        var code = locale.stripExtensions().toLanguageTag().replace('-', '_')
        while (true) {
            packs[code]?.let { return it.language }
            if ('_' !in code) break
            code = code.substringBeforeLast('_')
        }
        return if (locale.language == "zh") chinese else english
    }

    fun configured(context: Context): Context {
        val chosen = context.getSharedPreferences("mobile_ui", Context.MODE_PRIVATE).getString("language", "system")
        if (chosen == null || chosen !in setOf("zh","en") && chosen !in bundled(context)) return context
        val locale = Locale.forLanguageTag(chosen.replace('_','-'))
        val override = Configuration().apply { setLocale(locale); setLayoutDirection(locale) }
        return context.createConfigurationContext(override)
    }
}
