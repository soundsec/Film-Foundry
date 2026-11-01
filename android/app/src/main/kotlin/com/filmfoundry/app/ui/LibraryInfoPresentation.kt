package com.filmfoundry.app.ui

import com.filmfoundry.app.session.LibraryFacts
import com.filmfoundry.app.session.LibraryOrigin
import com.filmfoundry.app.session.RollLibraryContents
import com.filmfoundry.workflow.ResourceAvailability
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

object LibraryInfoPresentation {
    fun frame(index: Int,english: Boolean)=if(english) "Frame ${index.toString().padStart(2,'0')}" else "画面 ${index.toString().padStart(2,'0')}"
    fun batch(id: String,english: Boolean)=(if(english) "Development · " else "冲洗 · ")+id.take(8).uppercase(java.util.Locale.ROOT)
    fun scan(id: String,english: Boolean)=(if(english) "Scan · " else "扫描 · ")+id.take(8).uppercase(java.util.Locale.ROOT)
    fun filmTitle(roll: String,index: Int,resultId: String,english: Boolean)=
        "$roll\n${frame(index,english)} · ${RollLibraryContents.batchId(resultId).take(8).uppercase(java.util.Locale.ROOT)}"
    fun photoTitle(roll: String,index: Int,scanId: String,english: Boolean)=
        "$roll\n${frame(index,english)} · ${scanId.take(8).uppercase(java.util.Locale.ROOT)}"
    fun availability(value: ResourceAvailability,english: Boolean)=when(value) {
        ResourceAvailability.AVAILABLE -> if(english) "Available" else "可用"
        ResourceAvailability.REMOVED -> if(english) "Removed or not recorded" else "已删除或未收录"
        ResourceAvailability.MISSING_FILE -> if(english) "File missing · record retained" else "文件缺失 · 记录保留"
    }
    fun origin(o: LibraryOrigin,english: Boolean): String {
        val source=if(english) "Roll: ${o.rollLabel}\n${frame(o.frameIndex,true)} · ${availability(o.exposure,true)}" else
            "来源胶卷：${o.rollLabel}\n${frame(o.frameIndex,false)} · 曝光${availability(o.exposure,false)}"
        val film="${batch(o.batchId,english)}\n"+availability(o.developed,english)
        val photo=o.scanId?.let { scan(it,english)+"\n"+time(LibraryFacts(time=o.scanTime),english) }
        return listOfNotNull(source,film,photo).joinToString("\n\n")
    }
    fun dimensions(f: LibraryFacts,english: Boolean)=when {
        f.mixedSizes -> if(english) "Mixed sizes" else "多种尺寸"
        f.width!=null && f.height!=null -> (if(f.predicted) if(english) "Planned " else "预计 " else "")+"${f.width}×${f.height}"
        else -> if(english) "Size not recorded" else "尺寸未记录"
    }
    fun nature(f: LibraryFacts,english: Boolean,photo: Boolean=false,material: Boolean=false): String {
        if(f.polarity !in listOf("positive","negative")) return when {
            photo -> if(english) "Source type not recorded" else "来源性质未记录"
            material -> if(english) "Stock type not recorded" else "材料性质未记录"
            else -> if(english) "Type not recorded" else "性质未记录"
        }
        val label=when(f.polarity) { "positive" -> if(english) "Positive" else "正片";"negative" -> if(english) "Negative" else "负片";else -> if(english) "Not recorded" else "性质未记录" }
        return when { photo -> if(english) "Source: $label" else "来源$label";material -> if(english) "$label stock" else "${label}材料";else -> label }
    }
    fun time(f: LibraryFacts,english: Boolean,zone: ZoneId=ZoneId.systemDefault()): String {
        val value=f.time?.let { runCatching { DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm").withZone(zone).format(Instant.parse(it)) }.getOrNull() }
            ?: return if(english) "Time not recorded" else "时间未记录"
        return value+if(f.fileTime) if(english) " · file time" else " · 文件时间" else ""
    }
    fun brief(f: LibraryFacts,english: Boolean,photo: Boolean=false,material: Boolean=false)=
        dimensions(f,english)+" · "+nature(f,english,photo,material)+"\n"+time(f,english)
    fun tile(f: LibraryFacts,english: Boolean,photo: Boolean=false): String {
        val date=if(f.fileTime) f.time?.let { runCatching {
            (if(english) "File " else "文件 ")+DateTimeFormatter.ofPattern("MM-dd HH:mm").withZone(ZoneId.systemDefault()).format(Instant.parse(it))
        }.getOrNull() } ?: time(f,english) else time(f,english)
        return dimensions(f,english)+"\n"+nature(f,english,photo)+"\n"+date
    }
}
