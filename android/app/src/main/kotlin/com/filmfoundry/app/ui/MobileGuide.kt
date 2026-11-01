package com.filmfoundry.app.ui

import android.graphics.*
import android.view.View
import android.widget.LinearLayout
import android.widget.HorizontalScrollView
import android.widget.Button

enum class GuideTopic(val zh: String,val en: String,val leadZh: String,val leadEn: String) {
    CAPTURE("拍摄","Capture","装卷 → 拍摄 → 过片；未拍完可随时继续。","Load → shoot → advance. Resume unfinished rolls anytime."),
    DEVELOP("冲洗","Develop","选一卷已封胶卷，使用预设，或打开自由冲洗台组合药浴。","Open a sealed roll. Pick a process or arrange baths in Free processing."),
    VIEW("观片与扫描","View & scan","观片看底片；扫描调整风格并保存照片；导出另存文件。","View the negative, scan it into a photo, then export a file when needed."),
    RESOURCES("资源保留","Storage","曝光整卷管理；底片和照片逐张管理，删除互不连带。","Manage exposures by roll; negatives and photos individually. Each layer is independent."),
    ORGANIZE("浏览整理","Browse","长按进入多选；左右滑动浏览，双指放大看细节。","Hold to select, swipe to browse, and pinch to inspect details.");
    companion object {
        fun forPage(key: String)=when(key) {
            "rolls","roll_detail","develop","settings_recipes" -> DEVELOP
            "roll_batch","scan","viewer" -> VIEW
            "settings_process","settings_tools" -> RESOURCES
            "observe" -> ORGANIZE
            else -> CAPTURE
        }
    }
}

/** Offline, project-drawn diagrams. No external images, live camera frames or stored photos. */
object MobileGuide {
    fun install(shell: MobileShell) {
        var step=0
        val cards=mutableListOf<LinearLayout>()
        val tabs=mutableListOf<Button>()
        val navigator=shell.card(MobileShell.Page.SETTINGS_GUIDE,"操作指南","Getting started")
        val choices=LinearLayout(shell.root.context)
        val strip=HorizontalScrollView(shell.root.context).apply { isHorizontalScrollBarEnabled=false;addView(choices) }
        navigator.addView(strip)
        fun show(topic: GuideTopic) {
            cards.forEachIndexed { i,card -> card.visibility=if(i==topic.ordinal) View.VISIBLE else View.GONE }
            tabs.forEachIndexed { i,button -> button.isSelected=i==topic.ordinal;shell.styleButton(button) }
            choices.post { val tab=tabs[topic.ordinal];strip.scrollTo((tab.left-strip.width/2+tab.width/2).coerceAtLeast(0),0) }
            shell.scrollToTop(MobileShell.Page.SETTINGS_GUIDE)
        }
        for(topic in GuideTopic.entries) tabs+=shell.action(choices,topic.zh,topic.en) { show(topic) }.apply { minWidth=shell.dp(88) }
        fun lesson(zh: String,en: String,names: List<Pair<String,String>>,zhText: String,enText: String) {
            val card=shell.card(MobileShell.Page.SETTINGS_GUIDE,zh,en)
            cards+=card
            val labels=names.map { shell.copy(it.first,it.second) }
            val topic=GuideTopic.entries[step]
            val drawing=GuideDiagram.scene(step++,shell.copy("zh","en")=="zh")
            card.addView(object: View(shell.root.context) {
                val p=Paint(Paint.ANTI_ALIAS_FLAG)
                init { contentDescription=labels.joinToString(" → ") }
                override fun onDraw(c: Canvas) {
                    val scale=minOf(width/360f,height/152f)
                    c.save();c.translate((width-360*scale)/2,(height-152*scale)/2);c.scale(scale,scale)
                    for(mark in drawing) {
                        p.color=GuideDiagram.color(mark.ink,shell.ivory);p.style=Paint.Style.FILL;p.strokeWidth=1.6f
                        when(mark) {
                            is GuideDiagram.Box -> { p.style=if(mark.fill) Paint.Style.FILL else Paint.Style.STROKE;c.drawRoundRect(mark.x,mark.y,mark.x+mark.w,mark.y+mark.h,mark.radius,mark.radius,p) }
                            is GuideDiagram.Circle -> { p.style=if(mark.fill) Paint.Style.FILL else Paint.Style.STROKE;c.drawCircle(mark.x,mark.y,mark.r,p) }
                            is GuideDiagram.Line -> c.drawLine(mark.x,mark.y,mark.x2,mark.y2,p)
                            is GuideDiagram.Label -> {
                                p.typeface=Typeface.create("sans-serif",Typeface.BOLD);p.textAlign=Paint.Align.CENTER;p.textSize=mark.size
                                if(p.measureText(mark.value)>mark.width) p.textSize*=mark.width/p.measureText(mark.value)
                                c.drawText(mark.value,mark.x,mark.y,p)
                            }
                            is GuideDiagram.Triangle -> {
                                val path=Path();path.moveTo(mark.points[0],mark.points[1]);path.lineTo(mark.points[2],mark.points[3]);path.lineTo(mark.points[4],mark.points[5]);path.close();c.drawPath(path,p)
                            }
                        }
                    };c.restore()
                }
            },LinearLayout.LayoutParams(-1,shell.dp(152)))
            shell.note(card,topic.leadZh,topic.leadEn)
            shell.explanation(card,"操作细节","More details") { shell.note(it,zhText,enText) }
        }
        lesson("01 · 拍摄","01 · Capture",listOf("装卷" to "Load","曝光" to "Expose","过片" to "Advance"),
            "先选材料和规格，再进入横向取景。手动过片时，拍完快门会锁住：过片拍下一格，或用多曝上弦继续曝光当前格。未拍完可退出，下次继续。\nEV 按住片刻上下滑，倍率左右滑；双击按钮恢复默认。取景中点击对焦测光、双指缩放。",
            "Choose stock and format, then shoot in landscape. With manual advance, the shutter locks after capture: Advance moves to the next frame; Multi re-cocks this frame. Leave and resume anytime.\nHold EV briefly and slide vertically; slide zoom horizontally. Double-tap the button to reset. Tap the finder to meter/focus; pinch to zoom.")
        lesson("02 · 冲洗","02 · Develop",listOf("封卷" to "Seal","选药液" to "Baths","底片" to "Negative"),
            "拍摄菜单封卷后，到胶卷库打开这一卷。先选工艺并查看顺序和预期底片；药浴条件、事故、追加片基漂白和扫描保存从准备页分别进入。打开「自由冲洗台」选一浴再编辑，或从工序页进入共用条件；返回先回上一层。交叉冲洗会与材料产生不同反应；支持的手机可主动记录摇晃。单浴点「完成」更新草稿，外层「应用到本次冲洗」才带回准备页。每次冲洗保留独立底片，完成后可查看数量、观片、扫描或导出。",
            "Seal from the camera menu, then open the roll in Rolls. Choose a process and review its order and expected film. Preparation opens bath conditions, incidents, additional base bleaching and scan/save as child pages. In Free processing, select a bath to edit it or open shared conditions from the sequence; Back goes up a level. Cross-processing reacts with the stock; supported phones can record agitation. Done in a bath updates the draft; Apply to this development returns it to preparation. Completion shows counts with viewing, scanning and export entries.")
        lesson("03 · 观片与扫描","03 · View and scan",listOf("底片" to "Negative","扫描" to "Scan","照片" to "Photo"),
            "灯台保留底片原极性，旁边「灯光」可调亮度和色温。实景透射把透明底片覆盖在实时相机上，移动手机可透过底片看背景；正像投射将负片转成正像。先在预览下选扫描风格，再调光源、明暗（增益／对比度）、归一化、饱和度和曲线；修改后显示风格名与「自定义」。去片基和反相由最终底片自动匹配，也可手动开关，切换风格不改变它们。新版扫描光源曝光独立于自动定标：反相时EV降低更亮，不反相时EV升高更亮；明暗增益始终同向。预览角落的直方图可用右上按钮开关，显示当前成片预览的亮度与RGB分布；顶部保存生成软件图库照片，并保留当前编辑；撤销／重做可退回调整。风格可连续试选，取消恢复原状。照片菜单的「继续调整」从本照片保存参数开始；「输出」中可刷新或扫描本批。冲洗批次可「调整扫描参数」后扫描单张或本批。照片可导出文件；「保存投射到相册」写入手机相册。",
            "Light table preserves film polarity; Light adjusts brightness and temperature. Scene transmission places transparent film over the live camera: move the phone to see the background through it. Positive projection interprets negatives as positives. Choose a scan style below the preview, then adjust light, tone (gain/contrast), normalization, saturation and curves; edited style parameters add Custom to its name. Final film sets base correction and inversion automatically; override either switch independently. Style changes keep both. New scans calibrate at neutral light exposure: lower Light EV brightens inverted scans; higher EV brightens non-inverted scans. Gain always brightens in the same direction. The histogram button toggles a corner overlay of brightness and RGB distributions from the displayed output preview. Save at the top creates a Library photo and keeps this edit open; Undo/Redo reverse edits. Try styles repeatedly, then cancel to restore the opening state. Continue editing on a saved photo restores its settings. Output contains Refresh and Scan batch. A development batch offers Adjust scan settings for one frame or the batch. Export saves a file; Save projection to gallery writes to the phone gallery.")
        lesson("04 · 三层资源","04 · Three resource layers",listOf("曝光" to "Exposure","底片" to "Negative","照片" to "Photo"),
            "删除胶卷会从列表移除这一卷，并删除整卷拍摄原片和潜影。底片与照片可逐张管理，删除其中一层不连带删除其他层。保留曝光可重新冲洗，保留底片可重新扫描；上游已删会提示缺失，已有照片仍能看。删除无法撤销，请核对确认框。",
            "Delete roll removes it from the list along with all captures and latents. Negatives and photos can be managed individually; deleting one layer keeps the others. Retained exposures can be developed again; negatives can be rescanned. Missing sources are reported while existing photos remain viewable. Deletion cannot be undone.")
        lesson("05 · 浏览与整理","05 · Browse and organize",listOf("长按" to "Hold","勾选" to "Select","管理" to "Manage"),
            "长按胶卷或缩略图进入多选，也可点「选择」。勾选可跨分页保留；全选针对当前筛选范围。删除前会显示数量和保留范围，返回键先退出选择。照片左右滑动切换，双指或双击缩放；放大时拖动查看细节。",
            "Hold a roll or thumbnail, or tap Select. Selection persists across pages; Select all uses the current filter. Deletion confirms the count and retained layers. Back exits selection first. Swipe photos to browse; pinch or double-tap to zoom, then drag to inspect.")
        val footer=shell.card(MobileShell.Page.SETTINGS_GUIDE,"继续操作","Continue")
        shell.action(footer,"返回刚才的页面","Back to what I was doing") { shell.back() }
        shell.onHelpRequested={ from -> show(GuideTopic.forPage(from.key));if(shell.selected!=MobileShell.Page.SETTINGS_GUIDE) shell.select(MobileShell.Page.SETTINGS_GUIDE) }
        show(GuideTopic.CAPTURE)
    }
}
