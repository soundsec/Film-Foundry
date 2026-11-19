package com.filmfoundry.app.ui

import android.graphics.*
import android.view.View
import android.widget.LinearLayout
import android.widget.Button

enum class GuideTopic(val zh: String,val en: String,val leadZh: String,val leadEn: String) {
    CAPTURE("拍摄","Capture","在拍摄首页点「装填胶卷并开始」，选材料和画幅。横屏取景时，轻点画面对焦，再按快门。手动过片时，点「过片」就能拍下一格。","On Camera, tap Load film to start and choose a stock and format. In the landscape finder, tap to focus and press the shutter. With manual advance, tap Advance for the next frame."),
    DEVELOP("冲洗","Develop","拍完后从相机菜单封卷，再到胶卷库打开它。点冲洗方案，左右滑动列表浏览分类，点一行采用。回到准备页确认药浴，再开始冲洗。","Seal the roll from the camera menu and open it in Rolls. In the process chooser, swipe across the list to browse categories and tap a recipe. Check the baths on the preparation screen before starting."),
    VIEW("观片与扫描","View & scan","打开冲洗结果，选一格底片进入扫描。底部横条可滑到「曲线」等工具；点预览上的展开按钮可放大看图。顶部保存会把这次扫描存入软件图库。","Open a development result and pick a frame to scan. Scroll the bottom strip to reach tools such as Curves. Tap Expand on the preview for a larger view. Save at the top adds the scan to Library."),
    RESOURCES("资源保留","Storage","想换药浴，保留原卷曝光；想重新调色，保留底片。清理之前先看确认框中的资源类型和数量。","Keep the roll exposures to try another process. Keep the negative to make another scan. Before deleting anything, check the resource type and count in the confirmation."),
    ORGANIZE("浏览整理","Browse","点缩略图看大图，左右滑动换张。双指放大后可拖动查看细节；回到列表，长按缩略图可多选。","Tap a thumbnail to open it and swipe to browse. Pinch to zoom, then drag to inspect. Back in the list, hold a thumbnail to select multiple items.");
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
        var selected=GuideTopic.CAPTURE
        fun show(topic: GuideTopic) {
            selected=topic
            cards.forEachIndexed { i,card -> card.visibility=if(i==topic.ordinal) View.VISIBLE else View.GONE }
            shell.selectEditorTab(tabs,topic.ordinal)
            shell.guideContent.scrollTo(0,0)
        }
        for(topic in GuideTopic.entries) tabs+=shell.editorTab(shell.guideTabs,topic.zh,topic.en) { show(topic) }
        shell.guideContent.turnPage={ delta -> GuideTopic.entries.getOrNull(selected.ordinal+delta)?.let(::show) }
        fun lesson(zh: String,en: String,names: List<Pair<String,String>>,zhText: String,enText: String) {
            val card=shell.card(MobileShell.Page.SETTINGS_GUIDE,zh,en)
            cards+=card
            val labels=names.map { shell.copy(it.first,it.second) }
            val topic=GuideTopic.entries[step]
            val drawing=GuideDiagram.scene(step++,shell.copy("zh","en")=="zh")
            shell.note(card,topic.leadZh,topic.leadEn).textSize=16f
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
            shell.explanation(card,"操作细节","More details") { shell.note(it,zhText,enText) }
        }
        lesson("01 · 拍摄","01 · Capture",listOf("装卷" to "Load","曝光" to "Expose","过片" to "Advance"),
            "首页区分当前在机胶卷、机身设置与下一卷默认值。当前卷的材料与规格已锁定；颗粒、老化和片边只影响新装卷。ISO 与快门在机身设置单独调整，用于之后曝光。先选材料和规格，再进入横向取景。手动过片时，拍完快门会锁住：过片拍下一格，或用多曝上弦继续曝光当前格。未拍完可退出，下次继续。\nEV／快门按住片刻上下滑，倍率左右滑；双击按钮恢复默认／自动。取景中点击对焦测光、双指缩放。首次横屏有短暂手势提示，点按只收起提示；上方 ? 可重开并保留到点按或返回。",
            "Camera home separates the loaded roll, camera settings and next-roll defaults. Material and format stay frozen; grain, aging and edge defaults affect new rolls only. ISO & shutter controls subsequent exposures separately. Choose stock and format, then shoot in landscape. With manual advance, the shutter locks after capture: Advance moves to the next frame; Multi re-cocks this frame. Leave and resume anytime.\nHold EV/shutter briefly and slide vertically; slide zoom horizontally. Double-tap to restore default/auto. Tap the finder to meter/focus; pinch to zoom. A brief guide appears on the first landscape visit; tapping only closes it. The ? above the finder reopens it until you tap or go back.")
        lesson("02 · 冲洗","02 · Develop",listOf("封卷" to "Seal","选药液" to "Baths","底片" to "Negative"),
            "拍摄菜单封卷后，到胶卷库打开这一卷。先选工艺并查看顺序和预期底片；药浴条件、事故、追加片基漂白和扫描保存从准备页分别进入。打开「自由冲洗台」选一浴再编辑，或从工序页进入共用条件；系统返回先回上一层。交叉冲洗会与材料产生不同反应；支持的手机可主动记录摇晃。单浴点「完成」更新草稿，外层「应用到本次冲洗」才带回准备页。离开外层有未应用修改时，可选应用并返回、放弃修改或继续编辑；应用无效时保留草稿。每次冲洗保留独立底片，完成后可查看数量、观片、扫描或导出。",
            "Seal from the camera menu, then open the roll in Rolls. Choose a process and review its order and expected film. Preparation opens bath conditions, incidents, additional base bleaching and scan/save as child pages. In Free processing, select a bath to edit it or open shared conditions from the sequence; system Back goes up a level. Cross-processing reacts with the stock; supported phones can record agitation. Done in a bath updates the draft; Apply to this development returns it to preparation. Leaving with pending changes offers Apply & return, Discard, or Keep editing; invalid settings keep the draft open. Completion shows counts with viewing, scanning and export entries.")
        lesson("03 · 观片与扫描","03 · View and scan",listOf("底片" to "Negative","扫描" to "Scan","照片" to "Photo"),
            "灯台保留底片原极性，旁边「灯光」可调亮度和色温。实景透射把透明底片覆盖在实时相机上，移动手机可透过底片看背景；正像投射将负片转成正像。点按或上滑「扫描调整」展开抽屉，下滑收起即可看大图。先在「底片」选扫描风格，再调光源、明暗（增益／对比度）、归一化、饱和度和曲线；修改后显示风格名与「自定义」。去片基和反相由最终底片自动匹配，也可手动开关，切换风格不改变它们。新版扫描光源曝光独立于自动定标：反相时EV降低更亮，不反相时EV升高更亮；明暗增益始终同向。预览角落的直方图可用顶部工具栏按钮开关，显示当前成片预览的亮度与RGB分布；顶部保存生成软件图库照片，并保留当前编辑；撤销／重做可退回调整。风格可连续试选，取消恢复原状。照片菜单的「继续调整」从本照片保存参数开始；「输出」中可刷新或扫描本批。冲洗批次可「调整扫描参数」后扫描单张或本批。照片可导出文件；「保存投射到相册」写入手机相册。",
            "Light table preserves film polarity; Light adjusts brightness and temperature. Scene transmission places transparent film over the live camera: move the phone to see the background through it. Positive projection interprets negatives as positives. Tap or swipe up on Scan controls to open the drawer. Swipe down to see the large photo. Choose a style under Film, then adjust light, tone (gain/contrast), normalization, saturation and curves; edited style parameters add Custom to its name. Final film sets base correction and inversion automatically; override either switch independently. Style changes keep both. New scans calibrate at neutral light exposure: lower Light EV brightens inverted scans; higher EV brightens non-inverted scans. Gain always brightens in the same direction. The histogram button in the top toolbar toggles a corner overlay of brightness and RGB distributions from the displayed output preview. Save at the top creates a Library photo and keeps this edit open; Undo/Redo reverse edits. Try styles repeatedly, then cancel to restore the opening state. Continue editing on a saved photo restores its settings. Output contains Refresh and Scan batch. A development batch offers Adjust scan settings for one frame or the batch. Export saves a file; Save projection to gallery writes to the phone gallery.")
        lesson("04 · 三层资源","04 · Three resource layers",listOf("曝光" to "Exposure","底片" to "Negative","照片" to "Photo"),
            "删除胶卷会从列表移除这一卷，并删除整卷拍摄原片和潜影。底片与照片可逐张管理，删除其中一层不连带删除其他层。保留曝光可重新冲洗，保留底片可重新扫描；上游已删会提示缺失，已有照片仍能看。删除无法撤销，请核对确认框。",
            "Delete roll removes it from the list along with all captures and latents. Negatives and photos can be managed individually; deleting one layer keeps the others. Retained exposures can be developed again; negatives can be rescanned. Missing sources are reported while existing photos remain viewable. Deletion cannot be undone.")
        lesson("05 · 浏览与整理","05 · Browse and organize",listOf("长按" to "Hold","勾选" to "Select","管理" to "Manage"),
            "长按胶卷或缩略图进入多选，也可点「选择」。勾选可跨分页保留；全选针对当前筛选范围。删除前会显示数量和保留范围，返回键先退出选择。照片左右滑动切换，双指或双击缩放；放大时拖动查看细节。",
            "Hold a roll or thumbnail, or tap Select. Selection persists across pages; Select all uses the current filter. Deletion confirms the count and retained layers. Back exits selection first. Swipe photos to browse; pinch or double-tap to zoom, then drag to inspect.")
        for(card in cards) shell.action(card,"返回刚才的页面","Back to what I was doing") { shell.back() }
        shell.onHelpRequested={ from ->
            if(shell.selected!=MobileShell.Page.SETTINGS_GUIDE) shell.select(MobileShell.Page.SETTINGS_GUIDE)
            show(GuideTopic.forPage(from.key))
        }
        show(GuideTopic.CAPTURE)
    }
}
