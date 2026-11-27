package com.filmfoundry.app.ui

import android.content.Context
import android.content.res.Configuration
import android.util.Log
import android.util.AtomicFile
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.InputStream
import org.json.JSONTokener
import org.json.JSONObject
import java.util.Locale

/** Frontend copy from a Film Foundry v1 dictionary. */
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
    private data class Pack(val code: String,val name: String, val language: MobileLanguage)
    private val codePattern = Regex("[a-z]{2,3}(?:_[A-Z][a-z]{3})?(?:_(?:[A-Z]{2}|[0-9]{3}))?")
    private val reserved = setOf("zh", "en", "zh_CN", "en_US")
    private val chinese = MobileLanguage(true)
    private val english = MobileLanguage(false)
    @Volatile private var cached: Map<String, Pack>? = null

    private fun bundled(context: Context): Map<String, Pack> =
        cached ?: synchronized(this) {
            cached ?: read(context).also { cached = it }
        }

    private fun bytes(input: InputStream): ByteArray {
        val out=ByteArrayOutputStream()
        val buffer=ByteArray(8192)
        while(true) {
            val n=input.read(buffer)
            if(n<0) break
            require(out.size()+n<=8*1024*1024) { "Language pack exceeds 8 MiB" }
            out.write(buffer,0,n)
        }
        return out.toByteArray()
    }

    private fun parse(bytes: ByteArray): Pack {
        val json=JSONTokener(bytes.toString(Charsets.UTF_8).removePrefix("\uFEFF"))
        val data=json.nextValue() as? JSONObject ?: throw IllegalArgumentException("Expected a language dictionary")
        require(json.nextClean()=='\u0000' && data.getString("format")=="film-foundry-language" && data.get("version")==1)
        val code=data.getString("code")
        require(codePattern.matches(code) && code !in reserved) { "Invalid language code" }
        val fallback=data.optString("fallback","en_US")
        require(fallback in setOf("en_US","zh_CN"))
        val name=data.getString("name").trim()
        require(name.length in 1..80)
        val entries=data.getJSONObject("translations")
        require(entries.length()<=20000)
        val translations=buildMap {
            for(key in entries.keys()) {
                val value=entries.get(key)
                require((key.startsWith("text:") || key.startsWith("key:")) && value is String && key.length<=20000 && value.length<=20000)
                if(value.isNotBlank()) put(key,value)
            }
        }
        return Pack(code,name,MobileLanguage(fallback=="zh_CN",translations))
    }

    private fun read(context: Context): Map<String, Pack> = buildMap {
        fun add(file: String, open: () -> InputStream) {
            if (!file.endsWith(".json")) return
            try {
                val pack = open().use { parse(bytes(it)) }
                if (file == pack.code + ".json") put(pack.code, pack)
            } catch (error: Exception) {
                Log.w("MobileLanguages", "Ignoring invalid language pack: " + file, error)
            }
        }
        for (name in context.assets.list("languages").orEmpty().sorted())
            add(name) { context.assets.open("languages/" + name) }
        val shared = context.getExternalFilesDir(null)?.resolve("languages")
        for (file in shared?.listFiles().orEmpty().sortedBy { it.name }) {
            add(file.name) { file.inputStream() }
        }
        val personal = File(context.filesDir, "languages")
        val names = personal.list().orEmpty().map { it.removeSuffix(".bak") }.toSortedSet()
        for (name in names) add(name) { AtomicFile(File(personal, name)).openRead() }
    }

    @Synchronized fun install(context: Context, input: InputStream): String {
        val contents = bytes(input)
        val pack = parse(contents)
        val directory = File(context.filesDir, "languages")
        check(directory.isDirectory || directory.mkdirs()) { "Could not create the languages directory" }
        val file = AtomicFile(File(directory, pack.code + ".json"))
        val stream = file.startWrite()
        try {
            stream.write(contents)
            file.finishWrite(stream)
        } catch (e: Throwable) {
            file.failWrite(stream)
            throw e
        }
        cached = null
        return pack.code
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
