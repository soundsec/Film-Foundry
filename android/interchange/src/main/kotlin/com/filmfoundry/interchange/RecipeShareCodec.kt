package com.filmfoundry.interchange

import org.json.JSONArray
import org.json.JSONObject
import org.json.JSONTokener
import java.io.ByteArrayOutputStream
import java.math.BigDecimal
import java.nio.charset.StandardCharsets
import java.util.Base64
import java.util.zip.DataFormatException
import java.util.zip.Deflater
import java.util.zip.Inflater

class ConfigShareException(message: String) : IllegalArgumentException(message)

object RecipeShareCodec {
    private const val MAX_DECODED_BYTES = 262144
    private const val MAX_INPUT_CHARS = 524288
    private val allowedFields = DataKernel.CONFIG_SHARE_FIELDS.toSet()
    private val objectFields = setOf("film", "develop", "scanner", "look", "film_border")
    private val aliases = mapOf("chemistry" to "develop")
    private val tokenChars = (
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
        ).toSet()

    fun encode(config: Map<String, Any?>): String {
        val envelope = linkedMapOf(
            "config" to normalizePayload(config),
            "kind" to DataKernel.CONFIG_SHARE_KIND,
            "version" to DataKernel.CONFIG_SHARE_VERSION,
        )
        val raw = canonicalJson(envelope).toByteArray(StandardCharsets.UTF_8)
        if(raw.size>MAX_DECODED_BYTES) throw ConfigShareException("config share payload is too large.")
        val token = Base64.getUrlEncoder().withoutPadding().encodeToString(zlibCompress(raw))
        return DataKernel.CONFIG_SHARE_PREFIX + token
    }

    fun decode(text: Any?): Map<String, Any?> {
        val input=text?.toString() ?: ""
        if(input.length>MAX_INPUT_CHARS) throw ConfigShareException("config share payload is too large.")
        var raw = input.replace(Regex("\\s+"), "")
        if (raw.startsWith("\uFEFF")) {
            raw = raw.substring(1)
        }
        if (raw.startsWith("FFC1:")) {
            throw ConfigShareException(
                "preset-name share codes are not supported; share the JSON file " +
                    "or encode the parameter values.",
            )
        }
        if (!raw.startsWith(DataKernel.CONFIG_SHARE_PREFIX)) {
            throw ConfigShareException(
                "config share must start with '${DataKernel.CONFIG_SHARE_PREFIX}'.",
            )
        }
        val body = raw.substring(DataKernel.CONFIG_SHARE_PREFIX.length)
        if (body.isEmpty()) {
            throw ConfigShareException("config share payload is empty.")
        }
        if ('/' in body || '\\' in body) {
            throw ConfigShareException(
                "config share must carry parameter values, not preset names.",
            )
        }
        if (body.any { it !in tokenChars }) {
            throw ConfigShareException("config share token is not url-safe base64.")
        }
        val padded = body + "=".repeat((4 - body.length % 4) % 4)
        val decoded = try {
            zlibDecompress(Base64.getUrlDecoder().decode(padded), MAX_DECODED_BYTES)
        } catch (exc: IllegalArgumentException) {
            throw ConfigShareException("config share payload is not valid compressed JSON.")
        } catch (exc: DataFormatException) {
            throw ConfigShareException("config share payload is not valid compressed JSON.")
        }
        val envelope = try {
            val parser=JSONTokener(String(decoded, StandardCharsets.UTF_8))
            JSONObject(parser).also { if(parser.nextClean()!='\u0000') throw ConfigShareException("Trailing data in config share.") }
        } catch (exc: Exception) {
            throw ConfigShareException("config share payload is not valid JSON.")
        }
        if (envelope.opt("kind") != DataKernel.CONFIG_SHARE_KIND) {
            throw ConfigShareException("config share kind is not film_foundry_config_share.")
        }
        val version = (envelope.opt("version") as? Number)?.toInt()
        if (version != DataKernel.CONFIG_SHARE_VERSION || (envelope.opt("version") as? Number)?.toDouble()!=version.toDouble()) {
            throw ConfigShareException(
                "unsupported config share version ${envelope.opt("version")}.",
            )
        }
        val config = envelope.opt("config")
        if (config !is JSONObject) {
            throw ConfigShareException("config share payload must be an object.")
        }
        return normalizePayload(jsonToValue(config) as Map<*, *>)
    }

    internal fun canonicalJson(value: Any?): String {
        return when (value) {
            null, JSONObject.NULL -> "null"
            is Boolean -> if (value) "true" else "false"
            is Number -> encodeNumber(value)
            is String -> encodeString(value)
            is Map<*, *> -> {
                val keys = value.keys.map { it.toString() }.sorted()
                keys.joinToString(",", "{", "}") { key ->
                    val item = if (value.containsKey(key)) {
                        value[key]
                    } else {
                        value.entries.firstOrNull { it.key.toString() == key }?.value
                    }
                    encodeString(key) + ":" + canonicalJson(item)
                }
            }
            is List<*> -> value.joinToString(",", "[", "]") { canonicalJson(it) }
            is JSONObject -> canonicalJson(jsonToValue(value))
            is JSONArray -> canonicalJson(jsonToValue(value))
            else -> throw ConfigShareException(
                "config share cannot encode ${value::class.simpleName}.",
            )
        }
    }

    private fun normalizePayload(config: Map<*, *>): Map<String, Any?> {
        val payload = linkedMapOf<String, Any?>()
        for ((rawKey, rawValue) in config) {
            val key = aliases[rawKey.toString()] ?: rawKey.toString()
            if (key !in allowedFields) {
                throw ConfigShareException("config share rejects unknown field '$rawKey'.")
            }
            if (key in payload) {
                throw ConfigShareException("config share field '$key' was supplied twice.")
            }
            val converted = jsonable(rawValue)
            if (key in objectFields && converted !is Map<*, *>) {
                throw ConfigShareException("config share $key must be an object.")
            }
            payload[key] = converted
        }
        return payload
    }

    private fun jsonable(value: Any?): Any? {
        return when (value) {
            null, JSONObject.NULL -> null
            is Boolean -> value
            is Number -> {
                val d = value.toDouble()
                if (d.isNaN() || d.isInfinite()) {
                    throw ConfigShareException("config share cannot contain NaN or Infinity.")
                }
                value
            }
            is String -> value
            is Map<*, *> -> value.entries.associate { it.key.toString() to jsonable(it.value) }
            is List<*> -> value.map { jsonable(it) }
            is JSONObject, is JSONArray -> jsonable(jsonToValue(value))
            else -> throw ConfigShareException(
                "config share cannot encode ${value::class.simpleName}.",
            )
        }
    }

    private fun jsonToValue(value: Any?): Any? {
        return when (value) {
            null, JSONObject.NULL -> null
            is JSONObject -> {
                val out = linkedMapOf<String, Any?>()
                val keys = value.keys()
                while (keys.hasNext()) {
                    val key = keys.next()
                    out[key] = jsonToValue(value.get(key))
                }
                out
            }
            is JSONArray -> (0 until value.length()).map { jsonToValue(value.get(it)) }
            else -> value
        }
    }

    private fun encodeNumber(value: Number): String {
        return when (value) {
            is Byte, is Short, is Int, is Long -> value.toLong().toString()
            else -> {
                val d = value.toDouble()
                if (d.isNaN() || d.isInfinite()) {
                    throw ConfigShareException("config share cannot contain NaN or Infinity.")
                }
                val plain = BigDecimal.valueOf(d).stripTrailingZeros().toPlainString()
                if ('.' !in plain && 'e' !in plain && 'E' !in plain) "$plain.0" else plain
            }
        }
    }

    private fun encodeString(value: String): String {
        val out = StringBuilder(value.length + 2)
        out.append('"')
        for (ch in value) {
            when (ch) {
                '"' -> out.append("\\\"")
                '\\' -> out.append("\\\\")
                '\b' -> out.append("\\b")
                '\u000C' -> out.append("\\f")
                '\n' -> out.append("\\n")
                '\r' -> out.append("\\r")
                '\t' -> out.append("\\t")
                else -> if (ch < ' ') {
                    out.append("\\u").append(ch.code.toString(16).padStart(4, '0'))
                } else {
                    out.append(ch)
                }
            }
        }
        out.append('"')
        return out.toString()
    }

    private fun zlibCompress(raw: ByteArray): ByteArray {
        val deflater = Deflater(9)
        deflater.setInput(raw)
        deflater.finish()
        val buffer = ByteArray(4096)
        val out = ByteArrayOutputStream()
        while (!deflater.finished()) {
            val n = deflater.deflate(buffer)
            if (n > 0) out.write(buffer, 0, n)
        }
        deflater.end()
        return out.toByteArray()
    }

    private fun zlibDecompress(blob: ByteArray, limit: Int): ByteArray {
        val inflater = Inflater()
        inflater.setInput(blob)
        val buffer = ByteArray(4096)
        val out = ByteArrayOutputStream()
        try {
            while (!inflater.finished()) {
                val n = inflater.inflate(buffer)
                if (n == 0) {
                    if (inflater.needsInput()) {
                        throw ConfigShareException(
                            "config share payload is not valid compressed JSON.",
                        )
                    }
                    break
                }
                if (out.size() + n > limit) {
                    throw ConfigShareException("config share payload is too large.")
                }
                out.write(buffer, 0, n)
            }
            if (!inflater.finished()) {
                throw ConfigShareException("config share payload is too large.")
            }
            if(inflater.remaining!=0) throw ConfigShareException("Trailing compressed data in config share.")
        } finally {
            inflater.end()
        }
        return out.toByteArray()
    }
}
