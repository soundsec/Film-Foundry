package com.filmfoundry.workflow

import java.time.LocalDateTime
import java.time.format.DateTimeFormatter

object RollNaming {
    fun validate(value: String): String=value.trim().also {
        require(it.isNotEmpty() && it.length <= 64 && it.none(Char::isISOControl)) { "roll name must contain 1–64 visible characters" }
    }
    fun atTime(time: LocalDateTime,existing: Collection<String>): String {
        val base=time.format(DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss"))
        var result=base;var n=2
        while(result in existing) { result="$base-$n";n++ }
        return result
    }
}
