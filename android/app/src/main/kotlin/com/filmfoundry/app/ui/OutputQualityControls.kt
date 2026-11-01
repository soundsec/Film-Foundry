package com.filmfoundry.app.ui

import android.app.AlertDialog
import android.widget.*
import androidx.activity.ComponentActivity

/** Encoding controls only; material precision and processing stay in the workflow. */
internal object OutputQualityControls {
    fun jpeg(activity: ComponentActivity,shell: MobileShell,parent: LinearLayout,initial: Int,changed: (Int)->Unit) {
        val caption=TextView(activity).apply {
            text=shell.copy("沿用原质量：$initial，选择下方档位后更改。","Keeping original quality $initial until a tier is selected.")
            visibility=if(initial in ScanOutputSettings.jpegLevels) android.view.View.GONE else android.view.View.VISIBLE
        }
        parent.addView(caption)
        val presets=RadioGroup(activity)
        ScanOutputSettings.jpegLevels.forEachIndexed { index,value -> presets.addView(RadioButton(activity).apply {
            id=android.view.View.generateViewId();text=shell.copy(listOf("低","中","高")[index],listOf("Low","Medium","High")[index])+" · $value"
            isChecked=initial==value
            setOnClickListener { caption.visibility=android.view.View.GONE;changed(value) }
        }) }
        parent.addView(presets)
        shell.note(parent,"低档文件更小，高档保留更多细节；像素尺寸不变。","Low produces smaller files; High retains more detail. Dimensions stay unchanged.")
    }

    fun scan(activity: ComponentActivity,shell: MobileShell,initial: ScanOutputSettings,automatic: Boolean,apply: (ScanOutputSettings)->Unit) {
        fun copy(zh: String,en: String)=shell.copy(zh,en)
        var chosen=initial
        val panel=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;setPadding(shell.dp(20),shell.dp(12),shell.dp(20),shell.dp(12)) }
        val format=Button(activity).apply { shell.styleButton(this) }
        val quality=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL }
        panel.addView(format);panel.addView(quality)
        fun paint() {
            format.text=(if(chosen.format=="JPEG") "JPG" else chosen.format)+" ▾"
            quality.removeAllViews()
            when(chosen.format) {
                "JPEG" -> jpeg(activity,shell,quality,chosen.jpegQuality) { chosen=chosen.copy(jpegQuality=it) }
                "PNG" -> {
                    val group=RadioGroup(activity)
                    for(bits in listOf(8,16)) group.addView(RadioButton(activity).apply {
                        id=android.view.View.generateViewId();text=copy("PNG $bits 位 · 无损","PNG $bits-bit · lossless");isChecked=chosen.bits==bits
                        setOnClickListener { chosen=chosen.copy(bits=bits) }
                    })
                    quality.addView(group)
                    shell.note(quality,"8位适合日常分享；16位保留更多扫描色阶，便于后续修图。","8-bit suits everyday sharing; 16-bit retains more scan gradations for later editing.")
                }
                else -> shell.note(quality,"TIFF · 16位无损，适合后续修图；导出时保存为文件。","TIFF · 16-bit lossless, suitable for editing; export as a file.")
            }
        }
        format.setOnClickListener {
            AlertDialog.Builder(activity).setTitle(copy("产出格式","Output format"))
                .setSingleChoiceItems(arrayOf("JPG","PNG","TIFF"),chosen.formatIndex) { dialog,index ->
                    chosen=chosen.select(index,chosen.qualityIndex);dialog.dismiss();paint()
                }.setNegativeButton(copy("取消","Cancel"),null).show()
        }
        paint()
        if(automatic) shell.note(panel,"用于之后的自动扫描。当前正在编辑的照片和已开始的任务保持原设置。","Used for future automatic scans. The photo being edited and started tasks retain their settings.")
        val scroll=ScrollView(activity).apply { addView(panel) }
        AlertDialog.Builder(activity).setTitle(copy(if(automatic) "自动扫描产出" else "扫描输出质量",if(automatic) "Automatic scan output" else "Scan output quality"))
            .setView(scroll).setNegativeButton(copy("取消","Cancel"),null)
            .setPositiveButton(copy("应用","Apply")) { _,_->apply(chosen) }.show()
    }
}
