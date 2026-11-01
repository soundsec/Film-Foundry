package com.filmfoundry.workflow

/** Classification reads material identity, never its marketing name or selected processing bath. */
object MaterialSelection {
    fun displayName(film: Map<String,Any?>,name: String,english: Boolean=false): String {
        val tags=tags(film)
        val type=if(english) (if(tags.color=="bw") "B&W" else "Color")+(if(tags.polarity=="positive") " reversal" else " negative")
            else (if(tags.color=="bw") "黑白" else "彩色")+(if(tags.polarity=="positive") "反转片" else "负片")
        val label=name.trim()
        return if(label.isBlank()) type else if(label.startsWith(type,true)) label else "$type · $label"
    }
    data class Tags(val polarity: String,val color: String)
    data class Filter(val polarity: String="all",val color: String="all",val origin: String="all") {
        init { require(polarity in listOf("all","negative","positive") && color in listOf("all","color","bw") && origin in listOf("all","official","custom")) }
        fun accepts(film: Map<String,Any?>,source: String): Boolean {
            val tags=tags(film)
            return (polarity=="all" || polarity==tags.polarity) && (color=="all" || color==tags.color) && (origin=="all" || origin==source)
        }
    }
    fun tags(film: Map<String,Any?>)=Tags(if(film["image_polarity"]?.toString()?.lowercase() in listOf("positive","slide","reversal")) "positive" else "negative",
        if(film["color_process"]?.toString()?.lowercase() in listOf("bw","black_white","monochrome")) "bw" else "color")
}
