package com.filmfoundry.app.ui

import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.view.View
import android.view.ViewGroup
import android.widget.*
import androidx.activity.ComponentActivity

/** Native view shell only. Pages stay mounted; navigation never recreates a workflow session. */
class MobileShell(private val activity: ComponentActivity, val ivory: Boolean, initialPage: String?) {
    enum class Page(val key: String, val zh: String, val en: String, val index: String) {
        START("start","拍摄","Camera","01"),LOADING("loading","装填","Load film",""),CAPTURE("capture","拍摄","Camera",""),
        ROLLS("rolls","胶卷库","Rolls","02"),ROLL_DETAIL("roll_detail","胶卷详情","Roll details",""),ROLL_BATCH("roll_batch","冲洗结果","Development results",""),DEVELOP("develop","冲洗","Develop",""),
        OBSERVE("observe","图库","Library","03"),VIEWER("viewer","查看","View",""),SCAN("scan","扫描","Scan",""),
        SETTINGS("settings","设置","Settings","04"),
        SETTINGS_PROCESS("settings_process","后置处理","Processing",""),
        SETTINGS_RECIPES("settings_recipes","配方","Recipes",""),
        SETTINGS_TOOLS("settings_tools","附加工具","Tools",""),
        SETTINGS_GUIDE("settings_guide","使用指南","Guide",""),
        SETTINGS_ABOUT("settings_about","关于软件","About","");
        val parent: Page get()=when(this) {
            LOADING,CAPTURE -> START
            ROLL_DETAIL,ROLL_BATCH,DEVELOP -> ROLLS
            SCAN,VIEWER -> OBSERVE
            SETTINGS_PROCESS,SETTINGS_RECIPES,SETTINGS_TOOLS,SETTINGS_ABOUT,SETTINGS_GUIDE -> SETTINGS
            else -> this
        }
    }
    private val bg=Color.parseColor(if(ivory) "#F2EEE5" else "#141414")
    private val surface=Color.parseColor(if(ivory) "#FAF7F0" else "#20201F")
    private val ink=Color.parseColor(if(ivory) "#292721" else "#EFEAE1")
    private val muted=Color.parseColor(if(ivory) "#686257" else "#B5AEA1")
    private val accent=Color.parseColor(if(ivory) "#815C2E" else "#D7B278")
    private val line=Color.parseColor(if(ivory) "#D8D1C4" else "#383631")
    // Keep stateful backgrounds across status updates and own no button lifetime.
    private val styledButtons=java.util.WeakHashMap<Button,Pair<Boolean,String?>>()
    private val navigationIcons=java.util.WeakHashMap<Button,Pair<NavigationIcon,Boolean>>()
    val root=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;setBackgroundColor(bg) }
    private val selectionDock=FrameLayout(activity).apply { visibility=View.GONE;setBackgroundColor(surface);setPadding(dp(12),dp(8),dp(12),dp(8)) }
    private val heading=TextView(activity)
    private val body=FrameLayout(activity)
    val captureDock=FrameLayout(activity).apply { setPadding(dp(8),dp(8),dp(8),dp(8));setBackgroundColor(Color.BLACK) }
    val captureHud=LinearLayout(activity)
    private val captureLensControls=FrameLayout(activity)
    private val captureQuickHost=FrameLayout(activity)
    private var quick: FinderQuickControls?=null
    val quickControls: FinderQuickControls get()=quick ?: FinderQuickControls(this).also { quick=it }
    var onCaptureTouchDown: (Float,Float)->Unit={_,_->}
    private val captureTrigger=FrameLayout(activity)
    private val captureAdvance=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;gravity=android.view.Gravity.CENTER_HORIZONTAL }
    private val captureMenu=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL }
    private val captureDetails=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;visibility=View.GONE }
    private var captureDetailsMounted=false
    private val menuScroll=ScrollView(activity)
    private val menuArrow=Button(activity)
    private val header=LinearLayout(activity)
    private val navigation=LinearLayout(activity)
    private val task=LinearLayout(activity)
    private val pages=linkedMapOf<Page,LinearLayout>()
    private val scrolls=linkedMapOf<Page,View>()
    private val history=mutableListOf<Page>()
    val cameraSurface=FrameLayout(activity)
    val viewerSurface=FrameLayout(activity)
    val scanPreviewSurface=FrameLayout(activity).apply { setBackgroundColor(0xFF161616.toInt()) }
    val scanOverlaySurface=FrameLayout(activity)
    val scanPresetBar=LinearLayout(activity).apply { orientation=LinearLayout.HORIZONTAL;gravity=android.view.Gravity.CENTER_VERTICAL;setPadding(dp(12),dp(2),dp(12),dp(2));setBackgroundColor(surface) }
    val scanTools=LinearLayout(activity).apply { orientation=LinearLayout.HORIZONTAL;setPadding(dp(20),dp(6),dp(20),dp(18));setBackgroundColor(surface) }
    val scanActions=LinearLayout(activity).apply { orientation=LinearLayout.HORIZONTAL;setPadding(dp(12),dp(2),dp(12),dp(6));setBackgroundColor(surface) }
    val scanUndo=Button(activity)
    val scanRedo=Button(activity)
    val scanTitle=TextView(activity)
    val scanFocusButton=Button(activity)
    val scanResultBar=LinearLayout(activity).apply { orientation=LinearLayout.HORIZONTAL;visibility=View.GONE;setPadding(dp(8),0,dp(8),0) }
    private var scanToolScroller: View?=null
    private var scanFocused=false
    private var scanScroller: ScrollView?=null
    private var scanAspect=1.5f
    val developHeader=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;setPadding(dp(20),dp(8),dp(20),dp(8));setBackgroundColor(surface) }
    val developResultBar=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;visibility=View.GONE;setPadding(dp(16),0,dp(16),dp(4));setBackgroundColor(surface) }
    val developSteps=LinearLayout(activity).apply { orientation=LinearLayout.HORIZONTAL;setPadding(dp(12),dp(4),dp(12),dp(4)) }
    val developActions=LinearLayout(activity).apply { orientation=LinearLayout.HORIZONTAL;setPadding(dp(12),dp(6),dp(12),dp(6));setBackgroundColor(surface) }
    private var developScroller: ScrollView?=null
    val loadingSurface=FrameLayout(activity).apply { setBackgroundColor(Color.BLACK) }
    private val nav=linkedMapOf<Page,TextView>()
    val status=TextView(activity)
    val pause=Button(activity)
    private val progress=ProgressBar(activity,null,android.R.attr.progressBarStyleHorizontal)
    private val appearanceControls=mutableListOf<View>()
    var selected=(Page.entries.firstOrNull { it.key == initialPage && it != Page.LOADING } ?: Page.START)
        .let { when(it) { Page.VIEWER,Page.SCAN->Page.OBSERVE;Page.ROLL_DETAIL,Page.ROLL_BATCH->Page.ROLLS;else->it } }
        private set
    var onPageChanged: (Page)->Unit={}
    var onBeforeBack: ()->Boolean={ false }
    var onHelpRequested: (Page)->Unit={ select(Page.SETTINGS_GUIDE) }
    fun openGuide(from: Page=selected) { onHelpRequested(from) }
    fun scrollToTop(page: Page) { (when(page) { Page.SCAN->scanScroller;Page.DEVELOP->developScroller;else->scrolls[page] as? ScrollView })?.let { scroll -> scroll.post { scroll.scrollTo(0,0) } } }
    fun copy(zh: String,en: String)=if(activity.resources.configuration.locales[0].language == "zh") zh else en
    fun dp(value: Int)=(value*activity.resources.displayMetrics.density).toInt()
    /** Fixed finder instruments fit their own cells; normal UI text keeps system font scaling. */
    fun fitFinderText(view: TextView,sizeSp: Float,minDp: Int=10) {
        val minimum=dp(minDp).coerceAtLeast(1)
        val maximum=android.util.TypedValue.applyDimension(android.util.TypedValue.COMPLEX_UNIT_SP,sizeSp,activity.resources.displayMetrics).toInt().coerceAtLeast(minimum+1)
        view.setSingleLine(true);view.includeFontPadding=true
        view.setAutoSizeTextTypeUniformWithConfiguration(minimum,maximum,1,android.util.TypedValue.COMPLEX_UNIT_PX)
    }
    fun promote(view: View) {
        val parent=view.parent as? LinearLayout ?: return
        parent.removeView(view);parent.addView(view,0)
    }
    init {
        root.setOnApplyWindowInsetsListener { v,insets ->
            @Suppress("DEPRECATION")
            val bars=if(Build.VERSION.SDK_INT >= 30) insets.getInsets(android.view.WindowInsets.Type.systemBars() or android.view.WindowInsets.Type.displayCutout() or android.view.WindowInsets.Type.ime())
                else null
            @Suppress("DEPRECATION")
            v.setPadding(bars?.left ?: insets.systemWindowInsetLeft,bars?.top ?: insets.systemWindowInsetTop,
                bars?.right ?: insets.systemWindowInsetRight,bars?.bottom ?: insets.systemWindowInsetBottom)
            if(selected in listOf(Page.CAPTURE,Page.LOADING)) {
                var clearance=dp(16)
                if(Build.VERSION.SDK_INT>=31) {
                    val radius=listOf(android.view.RoundedCorner.POSITION_TOP_LEFT,android.view.RoundedCorner.POSITION_TOP_RIGHT,
                        android.view.RoundedCorner.POSITION_BOTTOM_LEFT,android.view.RoundedCorner.POSITION_BOTTOM_RIGHT)
                        .maxOf { insets.getRoundedCorner(it)?.radius ?: 0 }
                    clearance=maxOf(clearance,radius/2)
                }
                v.setPadding(maxOf(v.paddingLeft,clearance),maxOf(v.paddingTop,dp(8)),
                    maxOf(v.paddingRight,clearance),maxOf(v.paddingBottom,dp(8)))
            }
            if(Build.VERSION.SDK_INT>=31) {
                val radius=maxOf(insets.getRoundedCorner(android.view.RoundedCorner.POSITION_BOTTOM_LEFT)?.radius ?: 0,
                    insets.getRoundedCorner(android.view.RoundedCorner.POSITION_BOTTOM_RIGHT)?.radius ?: 0)
                val clearance=maxOf(dp(18),radius/2-(bars?.bottom ?: 0))
                scanTools.setPadding(dp(20),dp(6),dp(20),clearance)
            }
            if(selected !in listOf(Page.CAPTURE,Page.LOADING)) {
                val radius=if(Build.VERSION.SDK_INT>=31) maxOf(
                    insets.getRoundedCorner(android.view.RoundedCorner.POSITION_BOTTOM_LEFT)?.radius ?: 0,
                    insets.getRoundedCorner(android.view.RoundedCorner.POSITION_BOTTOM_RIGHT)?.radius ?: 0) else 0
                // One shared safe area covers navigation, selection and action docks.
                v.setPadding(v.paddingLeft,v.paddingTop,v.paddingRight,maxOf(v.paddingBottom+dp(12),radius/2))
            }
            insets
        }
        header.apply { orientation=LinearLayout.VERTICAL;setPadding(dp(20),dp(14),dp(20),dp(10)) }
        header.addView(TextView(activity).apply { text="FILM FOUNDRY";textSize=10f;letterSpacing=0.12f;setTextColor(muted) })
        val titleRow=LinearLayout(activity).apply { gravity=android.view.Gravity.CENTER_VERTICAL }
        val back=Button(activity).apply { text="‹";textSize=26f;minWidth=0;setPadding(0,0,0,0);contentDescription=copy("返回上一页","Go back");setOnClickListener { back() };tag="page_back" }
        navigationIcon(back,NavigationIcon.BACK)
        titleRow.addView(back,LinearLayout.LayoutParams(dp(48),dp(48)));heading.textSize=28f;heading.typeface=Typeface.DEFAULT_BOLD;heading.setTextColor(ink);titleRow.addView(heading,LinearLayout.LayoutParams(0,-2,1f))
        titleRow.addView(Button(activity).apply { text="?";contentDescription=copy("查看本页帮助","Help for this page");setOnClickListener { openGuide() };styleButton(this) },LinearLayout.LayoutParams(dp(48),dp(48)));header.addView(titleRow)
        root.addView(header)
        root.addView(body,LinearLayout.LayoutParams(-1,0,1f))
        for(page in Page.entries) {
            val content=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;setPadding(dp(16),dp(4),dp(16),dp(20)) }
            if(page==Page.SCAN) content.setPadding(dp(8),0,dp(8),dp(8))
            val scroll: View=if(page == Page.CAPTURE) object: FrameLayout(activity) {
                private var dismissingMenu=false
                override fun dispatchTouchEvent(event: android.view.MotionEvent): Boolean {
                    if(event.actionMasked==android.view.MotionEvent.ACTION_DOWN) {
                        onCaptureTouchDown(event.rawX,event.rawY)
                        fun inside(view: View): Boolean {
                            val bounds=android.graphics.Rect()
                            return view.isShown && view.getGlobalVisibleRect(bounds) && bounds.contains(event.rawX.toInt(),event.rawY.toInt())
                        }
                        dismissingMenu=menuScroll.visibility==View.VISIBLE && !inside(menuScroll) && !inside(menuArrow)
                        if(dismissingMenu) closeCaptureMenu()
                    }
                    if(dismissingMenu) {
                        if(event.actionMasked in listOf(android.view.MotionEvent.ACTION_UP,android.view.MotionEvent.ACTION_CANCEL)) dismissingMenu=false
                        return true // Closing a menu must not also focus, expose or advance film.
                    }
                    return super.dispatchTouchEvent(event)
                }
                override fun onMeasure(widthMeasureSpec: Int,heightMeasureSpec: Int) {
                    menuScroll.layoutParams?.width=minOf(dp(300),(MeasureSpec.getSize(widthMeasureSpec)-dp(224)).coerceAtLeast(1))
                    super.onMeasure(widthMeasureSpec,heightMeasureSpec)
                }
            }.apply {
                setBackgroundColor(Color.BLACK)
                addView(cameraSurface,FrameLayout.LayoutParams(-1,-1).apply { setMargins(dp(108),dp(8),dp(120),dp(62)) })
                addView(captureHud,FrameLayout.LayoutParams(dp(88),-1,android.view.Gravity.START).apply { setMargins(dp(8),dp(8),0,dp(8)) })
                addView(captureLensControls,FrameLayout.LayoutParams(-1,dp(52),android.view.Gravity.BOTTOM).apply { setMargins(dp(108),0,dp(120),dp(6)) })
                addView(captureQuickHost,FrameLayout.LayoutParams(-1,-1))
                menuScroll.addView(captureMenu);menuScroll.visibility=View.GONE
                menuScroll.background=rounded(0xF5121814.toInt(),16);menuScroll.elevation=dp(8).toFloat()
                menuScroll.clipToOutline=true;menuScroll.isFillViewport=false
                addView(menuScroll,FrameLayout.LayoutParams(dp(300),-2,android.view.Gravity.TOP or android.view.Gravity.END).apply { setMargins(dp(8),dp(12),dp(120),dp(12)) })
                addView(captureDock,FrameLayout.LayoutParams(dp(104),-1,android.view.Gravity.END).apply { rightMargin=dp(8) })
            } else if(page == Page.LOADING) loadingSurface
            else if(page == Page.VIEWER) viewerSurface
            else if(page == Page.SCAN) object: LinearLayout(activity) {
                init {
                    orientation=VERTICAL
                    scanActions.setPadding(dp(4),0,dp(4),0)
                    action(scanActions,"‹","‹") { back() }.apply {
                        tag="scan_toolbar";contentDescription=copy("返回上一页","Go back");styleButton(this)
                        layoutParams=LayoutParams(dp(48),dp(48))
                        navigationIcon(this,NavigationIcon.BACK)
                    }
                    scanTitle.apply { textSize=14f;maxLines=1;ellipsize=android.text.TextUtils.TruncateAt.END;setTextColor(ink) }
                    scanActions.addView(scanTitle,LayoutParams(0,-2,1f))
                    listOf(scanUndo to copy("撤销扫描调整","Undo scan edit"),scanRedo to copy("重做扫描调整","Redo scan edit")).forEachIndexed { index,(button,label) ->
                        button.text=if(index==0) "↶" else "↷";button.tag="scan_toolbar";button.contentDescription=label;button.tooltipText=label
                        styleButton(button);scanActions.addView(button,LayoutParams(dp(48),dp(48)));button.isEnabled=false
                    }
                    addView(scanActions,LayoutParams(-1,-2))
                    addView(scanPreviewSurface,LayoutParams(-1,dp(220)))
                    addView(scanResultBar,LayoutParams(-1,-2))
                    addView(scanPresetBar,LayoutParams(-1,-2))
                    addView(ScrollView(activity).apply { isFillViewport=true;clipToPadding=false;addView(content);scanScroller=this },LayoutParams(-1,0,1f))
                    addView(HorizontalScrollView(activity).apply { isHorizontalScrollBarEnabled=false;clipToPadding=false;addView(scanTools,ViewGroup.LayoutParams(-2,-2));scanToolScroller=this },LayoutParams(-1,-2))
                    scanPreviewSurface.addView(scanFocusButton.apply {
                        tag="scan_toolbar";contentDescription=copy("展开看图区域","Expand photo view");navigationIcon(this,NavigationIcon.EXPAND,true)
                        setOnClickListener { focusScan(!scanFocused) }
                    },FrameLayout.LayoutParams(dp(48),dp(48),android.view.Gravity.TOP or android.view.Gravity.START).apply { setMargins(dp(8),dp(8),dp(8),0) })
                    scanPreviewSurface.addView(scanOverlaySurface,FrameLayout.LayoutParams(-1,-1))
                }
                override fun onSizeChanged(w: Int,h: Int,oldw: Int,oldh: Int) {
                    super.onSizeChanged(w,h,oldw,oldh)
                    if(scanFocused) return
                    resizeScanPreview()
                }
            }
            else if(page==Page.DEVELOP) LinearLayout(activity).apply {
                orientation=LinearLayout.VERTICAL
                addView(developHeader,LinearLayout.LayoutParams(-1,-2))
                addView(developResultBar,LinearLayout.LayoutParams(-1,-2))
                addView(HorizontalScrollView(activity).apply { isHorizontalScrollBarEnabled=false;addView(developSteps) },LinearLayout.LayoutParams(-1,-2))
                addView(ScrollView(activity).apply { isFillViewport=true;clipToPadding=false;addView(content);developScroller=this },LinearLayout.LayoutParams(-1,0,1f))
                addView(developActions,LinearLayout.LayoutParams(-1,-2))
            }
            else ScrollView(activity).apply { isFillViewport=true;clipToPadding=false;addView(content) }
            pages[page]=content;scrolls[page]=scroll;body.addView(scroll,FrameLayout.LayoutParams(-1,-1))
        }
        val exit=Button(activity).apply { text="‹";contentDescription=copy("返回装填页","Back to loading");tag="camera_action";setOnClickListener { back() } }
        navigationIcon(exit,NavigationIcon.BACK,true)
        captureDock.addView(exit,FrameLayout.LayoutParams(-1,dp(48),android.view.Gravity.TOP))
        menuArrow.text="▴";menuArrow.tag="camera_action";menuArrow.contentDescription=copy("展开相机菜单","Open camera menu")
        navigationIcon(menuArrow,NavigationIcon.UP,true)
        menuArrow.setOnClickListener { closeQuickControls();val open=menuScroll.visibility != View.VISIBLE;showCaptureMenu(open) }
        captureDock.addView(menuArrow,FrameLayout.LayoutParams(-1,dp(48),android.view.Gravity.TOP).apply { topMargin=dp(48) })
        captureDock.addView(captureTrigger,FrameLayout.LayoutParams(dp(80),dp(80),android.view.Gravity.CENTER))
        captureDock.addView(captureAdvance,FrameLayout.LayoutParams(-1,dp(96),android.view.Gravity.BOTTOM))
        task.apply { orientation=LinearLayout.VERTICAL;setPadding(dp(18),dp(8),dp(18),dp(8));setBackgroundColor(surface) }
        progress.isIndeterminate=true;progress.visibility=View.GONE;progress.indeterminateTintList=ColorStateList.valueOf(accent)
        task.addView(progress,LinearLayout.LayoutParams(-1,dp(3)))
        val taskRow=LinearLayout(activity).apply { orientation=LinearLayout.HORIZONTAL;gravity=android.view.Gravity.CENTER_VERTICAL }
        status.textSize=12f;status.maxLines=2;status.ellipsize=android.text.TextUtils.TruncateAt.END;status.setTextColor(muted);status.setPadding(0,dp(4),dp(10),dp(4));status.accessibilityLiveRegion=View.ACCESSIBILITY_LIVE_REGION_POLITE
        status.setOnClickListener { if(status.text.isNotBlank()) android.app.AlertDialog.Builder(activity).setTitle(copy("任务状态","Task status")).setMessage(status.text).setPositiveButton(copy("知道了","OK"),null).show() }
        status.tooltipText=copy("点按查看完整任务状态","Tap for full task details")
        taskRow.addView(status,LinearLayout.LayoutParams(0,-2,1f))
        pause.text=copy("暂停","Pause");pause.visibility=View.GONE;styleButton(pause);taskRow.addView(pause,LinearLayout.LayoutParams(-2,-2))
        task.addView(taskRow);root.addView(task);root.addView(selectionDock)
        val captureStatus=TextView(activity).apply { textSize=12f;setTextColor(0xFFABB6A8.toInt());setPadding(dp(12),dp(8),dp(12),dp(8)) }
        captureDetails.addView(captureStatus)
        status.addTextChangedListener(object: android.text.TextWatcher {
            override fun beforeTextChanged(s: CharSequence?,start: Int,count: Int,after: Int)=Unit
            override fun onTextChanged(s: CharSequence?,start: Int,before: Int,count: Int) { captureStatus.text=s }
            override fun afterTextChanged(s: android.text.Editable?)=Unit
        })
        navigation.apply { orientation=LinearLayout.HORIZONTAL;setBackgroundColor(surface);gravity=android.view.Gravity.CENTER_VERTICAL;setPadding(dp(12),0,dp(12),dp(12)) }
        for(page in Page.entries.filter { it.parent == it }) {
            val item=TextView(activity).apply {
                text=copy(page.zh,page.en);textSize=12f;gravity=android.view.Gravity.CENTER
                minHeight=dp(56);setPadding(dp(2),dp(12),dp(2),dp(12));isFocusable=true
                contentDescription=copy(page.zh,page.en);setOnClickListener { select(page) }
                val icon=activity.getDrawable(when(page) { Page.START->android.R.drawable.ic_menu_camera;Page.ROLLS->android.R.drawable.ic_menu_agenda;Page.OBSERVE->android.R.drawable.ic_menu_gallery;else->android.R.drawable.ic_menu_preferences })?.mutate()
                icon?.setBounds(0,0,dp(22),dp(22));setCompoundDrawables(null,icon,null,null);compoundDrawablePadding=dp(4)
            }
            nav[page]=item;navigation.addView(item,LinearLayout.LayoutParams(0,-2,1f))
        }
        root.addView(navigation);select(selected)
    }
    fun select(page: Page,remember: Boolean=true) {
        closeQuickControls()
        if(page != selected) {
            if(page.parent != page && remember) history.add(selected) else if(page.parent == page) history.clear()
        }
        selected=page
        val capture=page == Page.CAPTURE || page == Page.LOADING
        root.setBackgroundColor(if(capture || page==Page.VIEWER) Color.BLACK else bg)
        root.requestApplyInsets()
        header.visibility=if(capture || page in listOf(Page.VIEWER,Page.SCAN)) View.GONE else View.VISIBLE
        navigation.visibility=if(capture || page in listOf(Page.VIEWER,Page.SCAN)) View.GONE else View.VISIBLE
        // Viewer keeps a fixed image viewport while a neighboring file loads.
        task.visibility=if(capture || page==Page.VIEWER || (page==Page.SCAN && progress.visibility!=View.VISIBLE)) View.GONE else View.VISIBLE
        if(page!=Page.CAPTURE) closeCaptureMenu()
        heading.text=copy(page.zh,page.en)
        header.findViewWithTag<View>("page_back")?.visibility=if(page.parent == page) View.GONE else View.VISIBLE
        for((key,scroll) in scrolls) scroll.visibility=if(key == page) View.VISIBLE else View.GONE
        for((key,item) in nav) {
            item.isSelected=key == page.parent;item.setTextColor(if(key == page.parent) accent else muted)
            item.typeface=if(key == page.parent) Typeface.DEFAULT_BOLD else Typeface.DEFAULT
            item.compoundDrawables.filterNotNull().forEach { it.setTint(if(key == page.parent) accent else muted) }
            item.background=if(key == page.parent) rounded(if(ivory) "#E9E0D1" else "#302B23",18) else null
        }
        onPageChanged(page)
    }
    fun back() { if(selected==Page.SCAN && scanFocused) { focusScan(false);return };if(closeCaptureMenu() || closeQuickControls() || onBeforeBack()) return;select(if(history.isNotEmpty()) history.removeAt(history.lastIndex) else if(selected.parent != selected) selected.parent else Page.START,false) }
    private fun showCaptureMenu(open: Boolean) {
        menuScroll.visibility=if(open) View.VISIBLE else View.GONE
        navigationIcon(menuArrow,if(open) NavigationIcon.DOWN else NavigationIcon.UP,true)
        menuArrow.contentDescription=if(open) copy("收起相机菜单","Close camera menu") else copy("展开相机菜单","Open camera menu")
        if(!open) { captureDetails.visibility=View.GONE;menuScroll.scrollTo(0,0) }
    }
    fun closeCaptureMenu(): Boolean {
        if(menuScroll.visibility!=View.VISIBLE) return false
        showCaptureMenu(false);return true
    }
    fun focusScan(focused: Boolean) {
        scanFocused=focused
        navigationIcon(scanFocusButton,if(focused) NavigationIcon.COLLAPSE else NavigationIcon.EXPAND,true)
        scanFocusButton.contentDescription=if(focused) copy("收起看图区域","Collapse photo view") else copy("展开看图区域","Expand photo view")
        listOf(scanPresetBar,scanScroller,scanToolScroller).forEach { it?.visibility=if(focused) View.GONE else View.VISIBLE }
        val parent=scanPreviewSurface.parent as? View ?: return
        scanPreviewSurface.layoutParams=(scanPreviewSurface.layoutParams as LinearLayout.LayoutParams).apply {
            weight=if(focused) 1f else 0f;height=if(focused) 0 else previewHeight(parent)
        }
    }
    fun scanImageSize(width: Int,height: Int) {
        if(width<=0 || height<=0) return
        scanAspect=width.toFloat()/height
        resizeScanPreview()
    }
    private fun previewHeight(parent: View): Int {
        val controls=scanActions.measuredHeight+scanPresetBar.measuredHeight+(scanToolScroller?.measuredHeight ?: dp(72))+scanResultBar.measuredHeight
        val available=(parent.height-controls-dp(190)).coerceAtLeast(dp(80))
        return (parent.width/scanAspect).toInt().coerceIn(minOf(dp(80),available),available)
    }
    private fun resizeScanPreview() {
        if(scanFocused) return
        val parent=scanPreviewSurface.parent as? View ?: return
        if(parent.width<=0 || parent.height<=0) return
        val wanted=previewHeight(parent)
        if(scanPreviewSurface.layoutParams.height!=wanted) scanPreviewSurface.layoutParams=scanPreviewSurface.layoutParams.apply { height=wanted }
    }
    fun showScanTaskStatus() { if(selected==Page.SCAN) task.visibility=View.VISIBLE }
    fun pageTitle(title: String) { heading.text=title }
    fun selectionPanel(panel: View,show: Boolean) {
        if(show) {
            if(panel.parent!==selectionDock) { (panel.parent as? ViewGroup)?.removeView(panel);selectionDock.removeAllViews();selectionDock.addView(panel,FrameLayout.LayoutParams(-1,-2)) }
            selectionDock.visibility=View.VISIBLE
        } else if(panel.parent===selectionDock) { selectionDock.removeView(panel);selectionDock.visibility=View.GONE }
    }
    /** Loading is transient; Back from the camera must never return to an expired loading draft. */
    fun enterLoadedCamera() { history.clear();select(Page.CAPTURE,false) }
    fun card(page: Page,zh: String,en: String,zhDetail: String="",enDetail: String=""): LinearLayout {
        val finder=page==Page.CAPTURE
        val panel=LinearLayout(activity).apply {
            orientation=LinearLayout.VERTICAL
            val inset=dp(if(finder) 10 else 16);setPadding(inset,inset,inset,inset)
            if(!finder) background=rounded(surface,22)
            layoutParams=LinearLayout.LayoutParams(-1,-2).apply { bottomMargin=dp(if(finder) 0 else 12) }
        }
        panel.addView(TextView(activity).apply { text=copy(zh,en);textSize=if(finder) 14f else 17f;typeface=Typeface.DEFAULT_BOLD;setTextColor(if(finder) Color.LTGRAY else ink);setPadding(0,0,0,dp(8)) })
        if(zhDetail.isNotBlank()) panel.addView(TextView(activity).apply { text=copy(zhDetail,enDetail);textSize=13f;setTextColor(muted);setPadding(0,0,0,dp(12)) })
        (if(page == Page.CAPTURE) captureMenu else pages.getValue(page)).addView(panel);return panel
    }
    fun settingsEntry(zh: String,en: String,zhDetail: String,enDetail: String,page: Page) {
        shortcut(pages.getValue(Page.SETTINGS),zh,en,zhDetail,enDetail) { select(page) }
    }
    /** Secondary navigation uses quiet rows; filled buttons remain for committing actions. */
    fun shortcut(parent: LinearLayout,zh: String,en: String,zhDetail: String="",enDetail: String="",run: ()->Unit): LinearLayout {
        val row=LinearLayout(activity).apply {
            orientation=LinearLayout.HORIZONTAL;gravity=android.view.Gravity.CENTER_VERTICAL
            minimumHeight=dp(64);setPadding(dp(16),dp(12),dp(12),dp(12))
            background=android.graphics.drawable.RippleDrawable(ColorStateList.valueOf(if(ivory) 0x22815C2E else 0x33FFFFFF),rounded(if(ivory) "#EBE5D9" else "#2A2926",16),null)
            isClickable=true;isFocusable=true;contentDescription=listOf(copy(zh,en),copy(zhDetail,enDetail)).filter { it.isNotBlank() }.joinToString(". ");setOnClickListener { run() }
        }
        val text=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL }
        text.addView(TextView(activity).apply { this.text=copy(zh,en);textSize=16f;typeface=Typeface.DEFAULT_BOLD;setTextColor(ink) })
        if(zhDetail.isNotBlank()) text.addView(TextView(activity).apply { this.text=copy(zhDetail,enDetail);textSize=12f;setTextColor(muted);setPadding(0,dp(4),0,0) })
        row.addView(text,LinearLayout.LayoutParams(0,-2,1f))
        row.addView(ImageView(activity).apply { setImageDrawable(NavigationIconDrawable(NavigationIcon.NEXT,ColorStateList.valueOf(accent)));importantForAccessibility=View.IMPORTANT_FOR_ACCESSIBILITY_NO },LinearLayout.LayoutParams(dp(32),dp(40)))
        parent.addView(row,LinearLayout.LayoutParams(-1,-2).apply { bottomMargin=dp(8) });return row
    }
    /** Expanding only reveals controls; it never enables or resets a recipe override. */
    fun disclosure(parent: LinearLayout,zh: String,en: String,zhDetail: String="",enDetail: String="",expanded: Boolean=false,onToggle: (Boolean)->Unit={}): LinearLayout {
        val content=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;visibility=if(expanded) View.VISIBLE else View.GONE }
        lateinit var row: LinearLayout
        fun update() {
            val open=content.visibility==View.VISIBLE
            (row.getChildAt(1) as ImageView).setImageDrawable(NavigationIconDrawable(if(open) NavigationIcon.DOWN else NavigationIcon.NEXT,ColorStateList.valueOf(accent)))
            row.contentDescription=listOf(copy(zh,en),copy(zhDetail,enDetail),copy(if(open) "收起" else "展开",if(open) "Collapse" else "Expand")).filter { it.isNotBlank() }.joinToString(" · ")
        }
        row=shortcut(parent,zh,en,zhDetail,enDetail) {
            val open=content.visibility!=View.VISIBLE;content.visibility=if(open) View.VISIBLE else View.GONE;update();onToggle(open)
        }
        parent.addView(content);update();return content
    }
    fun mountShutter(button: Button) { (button.parent as? ViewGroup)?.removeView(button);button.tag="shutter";captureTrigger.addView(button,FrameLayout.LayoutParams(dp(76),dp(76),android.view.Gravity.CENTER));styleButton(button) }
    fun mountExposure(row: LinearLayout) {
        captureMenu.addView(row,LinearLayout.LayoutParams(-2,dp(44)).apply { setMargins(dp(16),dp(8),0,dp(8)) })
        for(i in 0 until row.childCount) (row.getChildAt(i) as? Button)?.let {
            it.tag="capture_rail";it.layoutParams=LinearLayout.LayoutParams(dp(44),dp(44));styleButton(it)
        }
    }
    fun mountLensControls(row: View) { captureLensControls.removeAllViews();captureLensControls.addView(row,FrameLayout.LayoutParams(-1,-1)) }
    fun mountQuickControls(view: View) { captureQuickHost.addView(view,FrameLayout.LayoutParams(-1,-1)) }
    fun closeQuickControls(): Boolean { val opened=quick?.isOpen==true;quick?.dismiss();return opened }
    fun refreshQuickControls() { quick?.refreshConfirmed() }
    fun closeQuickControl(key: String) { quick?.dismissIf(key) }
    fun mountCaptureDetails(view: View) {
        if(!captureDetailsMounted) {
            captureDetailsMounted=true
            action(captureMenu,"拍摄状态","Capture status") { captureDetails.visibility=if(captureDetails.visibility==View.VISIBLE) View.GONE else View.VISIBLE }.apply {
                tag="capture_option";styleButton(this)
            }
            captureMenu.addView(captureDetails,LinearLayout.LayoutParams(-1,-2))
        }
        (view.parent as? ViewGroup)?.removeView(view)
        captureDetails.addView(view,LinearLayout.LayoutParams(-1,-2).apply { setMargins(dp(12),dp(4),dp(12),dp(6)) })
    }
    fun mountAdvance(button: Button,advance: Boolean=false) {
        (button.parent as? ViewGroup)?.removeView(button);button.tag=if(advance) "capture_advance" else "capture_rail";button.maxLines=1;button.setSingleLine(true)
        captureAdvance.addView(button,LinearLayout.LayoutParams(-1,dp(44)).apply { bottomMargin=dp(4) });styleButton(button)
    }
    fun note(parent: LinearLayout,zh: String,en: String)=TextView(activity).also {
        it.text=copy(zh,en);it.textSize=13f;it.setTextColor(muted);it.setPadding(0,dp(6),0,dp(10));parent.addView(it)
    }
    fun explanation(parent: LinearLayout,zh: String,en: String,fill: (LinearLayout)->Unit) {
        val content=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;visibility=View.GONE }
        val toggle=action(parent,"▸ $zh","▸ $en") { }
        toggle.setOnClickListener { val show=content.visibility!=View.VISIBLE;content.visibility=if(show) View.VISIBLE else View.GONE;toggle.text=(if(show) "▾ " else "▸ ")+copy(zh,en) }
        parent.addView(content);fill(content)
    }
    /** Persistent labels remain readable with a populated field, focus or the keyboard open. */
    fun resetButton(title: String,restore: ()->Unit)=Button(activity).apply {
        tag="parameter_reset";text="↺";styleButton(this)
        contentDescription=copy("恢复$title 初始值","Reset $title");tooltipText=contentDescription
        setOnClickListener { restore() }
    }
    fun resetSection(parent: LinearLayout,title: String,restore: ()->Unit): LinearLayout {
        val row=LinearLayout(activity).apply { gravity=android.view.Gravity.CENTER_VERTICAL }
        row.addView(TextView(activity).apply { text=title;textSize=14f;setTextColor(ink) },LinearLayout.LayoutParams(0,-2,1f))
        row.addView(resetButton(title,restore),LinearLayout.LayoutParams(dp(48),dp(48)))
        parent.addView(row,0);return row
    }
    fun field(parent: LinearLayout,input: EditText,title: String,detail: String="",restore: (()->Unit)?=null,resettable: Boolean=true): LinearLayout {
        val initial=input.text.toString()
        if(input.id==View.NO_ID) input.id=View.generateViewId()
        val group=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;tag=title }
        val header=LinearLayout(activity).apply { gravity=android.view.Gravity.CENTER_VERTICAL }
        header.addView(TextView(activity).apply {
            text=title;labelFor=input.id;textSize=14f;typeface=Typeface.DEFAULT_BOLD;setTextColor(ink)
            setPadding(0,dp(10),0,dp(6))
        },LinearLayout.LayoutParams(0,-2,1f))
        if(resettable) header.addView(resetButton(title) { input.error=null;if(restore!=null) restore() else input.setText(initial) },LinearLayout.LayoutParams(dp(48),dp(48)))
        group.addView(header)
        input.tag="labeled_field";input.hint=copy("留空沿用原值","Blank keeps the existing value")
        input.minHeight=dp(50);input.background=rounded(if(ivory) "#F5F0E7" else "#181817",12)
        input.setPadding(dp(12),dp(12),dp(12),dp(12));input.setTextColor(ink);input.setHintTextColor(muted);input.textSize=15f
        group.addView(input,LinearLayout.LayoutParams(-1,-2))
        if(detail.isNotBlank()) note(group,detail,detail)
        parent.addView(group,LinearLayout.LayoutParams(-1,-2).apply { bottomMargin=dp(8) })
        return group
    }
    fun fieldLabel(input: EditText)=((input.parent as? LinearLayout)?.tag as? String) ?: ((input.parent as? LinearLayout)?.getChildAt(0) as? TextView)?.text?.toString() ?: input.hint?.toString().orEmpty()
    fun action(parent: LinearLayout,zh: String,en: String,primary: Boolean=false,run: ()->Unit)=Button(activity).also {
        it.text=copy(zh,en);if(primary) it.tag="primary";styleButton(it,primary);it.setOnClickListener { run() };parent.addView(it)
    }
    /** Quiet selection chips, visually distinct from buttons that submit a task. */
    fun editorTab(parent: LinearLayout,zh: String,en: String,run: ()->Unit)=action(parent,zh,en,run=run).apply {
        tag="editor_tab";styleButton(this)
        layoutParams=LinearLayout.LayoutParams(-2,-2).apply { marginEnd=dp(6) }
    }
    fun listTile(parent: LinearLayout,title: String,detail: String,badge: String,mark: android.graphics.Bitmap?=null,run: ()->Unit): LinearLayout {
        val tile=LinearLayout(activity).apply {
            orientation=LinearLayout.VERTICAL;minimumHeight=dp(88);setPadding(dp(14),dp(14),dp(14),dp(14))
            background=android.graphics.drawable.RippleDrawable(ColorStateList.valueOf(if(ivory) 0x22815C2E else 0x33FFFFFF),rounded(if(ivory) "#EBE5D9" else "#2A2926",16),null)
            isClickable=true;isFocusable=true;contentDescription="$title, $detail, $badge";setOnClickListener { run() }
        }
        val row=LinearLayout(activity).apply { gravity=android.view.Gravity.CENTER_VERTICAL }
        if(mark != null) row.addView(android.widget.ImageView(activity).apply { setImageBitmap(mark);scaleType=android.widget.ImageView.ScaleType.CENTER_CROP;contentDescription=title },LinearLayout.LayoutParams(dp(56),dp(56)).apply { rightMargin=dp(12) })
        row.addView(TextView(activity).apply { text=title;textSize=16f;typeface=Typeface.DEFAULT_BOLD;setTextColor(ink) },LinearLayout.LayoutParams(0,-2,1f))
        row.addView(TextView(activity).apply { text=badge;textSize=15f;setTextColor(accent);setPadding(dp(8),0,0,0) });tile.addView(row)
        tile.addView(TextView(activity).apply { text=detail;textSize=12f;setTextColor(muted);setPadding(0,dp(8),0,0) })
        parent.addView(tile,LinearLayout.LayoutParams(-1,-2).apply { bottomMargin=dp(10) })
        return tile
    }
    fun navigationIcon(button: Button,icon: NavigationIcon,onImage: Boolean=false) {
        navigationIcons[button]=icon to onImage;styleButton(button)
    }
    fun styleButton(button: Button,primary: Boolean=false) {
        navigationIcons[button]?.let { (icon,onImage) ->
            button.text="";button.gravity=android.view.Gravity.CENTER;button.minWidth=0;button.minimumWidth=0;button.minHeight=dp(48)
            button.setPadding(0,0,0,0);button.backgroundTintList=null;button.stateListAnimator=null
            val colors=ColorStateList(arrayOf(intArrayOf(-android.R.attr.state_enabled),intArrayOf()),intArrayOf(muted,if(onImage) Color.WHITE else ink))
            button.background=android.graphics.drawable.RippleDrawable(ColorStateList.valueOf(if(ivory && !onImage) 0x22815C2E else 0x33FFFFFF),null,rounded(Color.WHITE,24))
            button.foreground=NavigationIconDrawable(icon,colors);return
        }
        if(button.tag=="scan_toolbar") {
            button.isAllCaps=false;button.setTextSize(android.util.TypedValue.COMPLEX_UNIT_PX,dp(22).toFloat());button.minWidth=0;button.minimumWidth=0;button.minHeight=dp(48)
            button.setPadding(0,0,0,0);button.setTextColor(ink);button.background=null
            return // The parent owns layout parameters, including FrameLayout gravity.
        }
        if(button.tag=="shutter") button.text=""
        val key=primary to button.tag?.toString()
        if(styledButtons[button]==key) return
        styledButtons[button]=key
        if(button.tag=="parameter_reset") {
            button.isAllCaps=false;button.setTextSize(android.util.TypedValue.COMPLEX_UNIT_PX,dp(24).toFloat());button.minWidth=dp(48);button.minimumWidth=dp(48);button.minHeight=dp(48)
            button.setPadding(0,0,0,0);button.setSingleLine(true);button.stateListAnimator=null
            button.backgroundTintList=null;button.setTextColor(accent)
            button.background=android.graphics.drawable.RippleDrawable(ColorStateList.valueOf(if(ivory) 0x22815C2E else 0x33D7B278),null,GradientDrawable().apply { shape=GradientDrawable.OVAL;setColor(Color.WHITE) })
            return
        }
        if(button.tag=="editor_tab") {
            button.isAllCaps=false;button.textSize=14f;button.typeface=Typeface.create("sans-serif-medium",Typeface.NORMAL)
            button.minWidth=dp(64);button.minHeight=dp(48);button.setSingleLine(false);button.maxLines=2;button.ellipsize=null
            button.setPadding(dp(16),dp(8),dp(16),dp(8));button.includeFontPadding=true;button.stateListAnimator=null
            val states=arrayOf(intArrayOf(-android.R.attr.state_enabled),intArrayOf(android.R.attr.state_selected),intArrayOf())
            val selectedInk=Color.parseColor(if(ivory) "#65451E" else "#F3D3A0")
            button.setTextColor(ColorStateList(states,intArrayOf(muted,selectedInk,muted)))
            fun chip(fill: Int,stroke: Int)=GradientDrawable().apply { setColor(fill);cornerRadius=dp(24).toFloat();setStroke(dp(1),stroke) }
            button.backgroundTintList=null
            button.background=android.graphics.drawable.StateListDrawable().apply {
                addState(states[0],chip(bg,line))
                addState(states[1],chip(Color.parseColor(if(ivory) "#EAD8B9" else "#493B2B"),accent))
                addState(states[2],chip(surface,line))
            }
            button.foreground=android.graphics.drawable.RippleDrawable(ColorStateList.valueOf(if(ivory) 0x22815C2E else 0x33D7B278),null,chip(Color.WHITE,Color.WHITE))
            button.refreshDrawableState();return
        }
        if(button.tag == "shutter") {
            button.foreground=null;button.stateListAnimator=null
            button.text="";button.minWidth=dp(72);button.minHeight=dp(72);button.setPadding(0,0,0,0)
            button.background=android.graphics.drawable.StateListDrawable().apply {
                addState(intArrayOf(-android.R.attr.state_enabled),GradientDrawable().apply { shape=GradientDrawable.OVAL;setColor(0xFF292B27.toInt());setStroke(dp(4),0xFF62665A.toInt()) })
                addState(intArrayOf(android.R.attr.state_pressed),GradientDrawable().apply { shape=GradientDrawable.OVAL;setColor(0xFFB89865.toInt());setStroke(dp(5),0xFFF8EBD3.toInt()) })
                addState(intArrayOf(),GradientDrawable(GradientDrawable.Orientation.TL_BR,intArrayOf(0xFFFFF7E8.toInt(),0xFFD9D6CB.toInt(),0xFFF1EBDE.toInt())).apply { shape=GradientDrawable.OVAL;setStroke(dp(4),0xFF7D8277.toInt()) })
            };button.backgroundTintList=null;return
        }
        if(button.tag == "capture_advance") {
            button.isAllCaps=false;button.textSize=13f;button.minWidth=0;button.minimumWidth=0;button.minHeight=dp(44)
            button.gravity=android.view.Gravity.CENTER;button.setPadding(dp(4),0,dp(4),0);button.setSingleLine(true)
            button.setAutoSizeTextTypeUniformWithConfiguration(10,13,1,android.util.TypedValue.COMPLEX_UNIT_SP)
            button.stateListAnimator=null;button.backgroundTintList=null
            val states=arrayOf(intArrayOf(-android.R.attr.state_enabled),intArrayOf(android.R.attr.state_pressed),intArrayOf())
            button.setTextColor(ColorStateList(states,intArrayOf(0xFF8C9288.toInt(),Color.BLACK,0xFFF4D29B.toInt())))
            button.background=android.graphics.drawable.StateListDrawable().apply {
                addState(states[0],GradientDrawable().apply { setColor(0xFF171B17.toInt());setStroke(dp(1),0xFF3E453A.toInt());cornerRadius=dp(12).toFloat() })
                addState(states[1],rounded(accent,12))
                addState(states[2],GradientDrawable().apply { setColor(0xFF3D3324.toInt());setStroke(dp(1),0xFFB89B65.toInt());cornerRadius=dp(12).toFloat() })
            }
            button.foreground=android.graphics.drawable.RippleDrawable(ColorStateList.valueOf(0x33FFFFFF),null,rounded(Color.WHITE,12));return
        }
        if(button.tag == "capture_rail" || button.tag == "camera_action") {
            button.stateListAnimator=null
            button.isAllCaps=false;button.textSize=if(button.tag=="capture_rail") 13f else 15f
            button.minWidth=0;button.minHeight=if(button.tag=="capture_rail") dp(40) else dp(48)
            if(button.tag=="capture_rail") { button.maxLines=1;button.setSingleLine(true);button.ellipsize=android.text.TextUtils.TruncateAt.END }
            button.setPadding(0,0,0,0)
            fitFinderText(button,if(button.tag=="capture_rail") 13f else 15f)
            val states=arrayOf(intArrayOf(-android.R.attr.state_enabled),intArrayOf(android.R.attr.state_selected),intArrayOf(android.R.attr.state_activated),intArrayOf(android.R.attr.state_pressed),intArrayOf())
            button.setTextColor(ColorStateList(states,intArrayOf(Color.GRAY,Color.BLACK,Color.BLACK,Color.BLACK,Color.WHITE)))
            button.background=rounded(Color.WHITE,12)
            button.backgroundTintList=ColorStateList(states,intArrayOf(Color.BLACK,accent,accent,accent,Color.BLACK))
            button.foreground=android.graphics.drawable.RippleDrawable(ColorStateList.valueOf(0x33FFFFFF),null,rounded(Color.WHITE,12));return
        }
        if(button.tag=="capture_option") {
            button.isAllCaps=false;button.textSize=14f;button.minWidth=0;button.minimumWidth=0;button.minHeight=dp(44)
            button.setSingleLine(false);button.maxLines=Int.MAX_VALUE;button.ellipsize=null;button.includeFontPadding=true
            button.gravity=android.view.Gravity.CENTER_VERTICAL or android.view.Gravity.START
            button.setPadding(dp(12),dp(8),dp(12),dp(8));button.stateListAnimator=null;button.backgroundTintList=null
            button.setTextColor(ColorStateList(arrayOf(intArrayOf(-android.R.attr.state_enabled),intArrayOf()),intArrayOf(0xFF7F897F.toInt(),Color.WHITE)))
            button.background=GradientDrawable().apply { setColor(0xFF222C24.toInt());cornerRadius=dp(10).toFloat() }
            button.foreground=android.graphics.drawable.RippleDrawable(ColorStateList.valueOf(0x33FFFFFF),null,rounded(Color.WHITE,10));return
        }
        button.isAllCaps=false;button.textSize=15f;button.minHeight=dp(52)
        button.setSingleLine(false);button.maxLines=Int.MAX_VALUE;button.ellipsize=null;button.includeFontPadding=true
        button.setPadding(dp(12),dp(10),dp(12),dp(10))
        val states=arrayOf(intArrayOf(-android.R.attr.state_enabled),intArrayOf(android.R.attr.state_selected),intArrayOf(android.R.attr.state_activated),intArrayOf(android.R.attr.state_pressed),intArrayOf(android.R.attr.state_focused),intArrayOf())
        button.setTextColor(ColorStateList(states,intArrayOf(muted,bg,bg,bg,bg,if(primary) bg else ink)))
        val fill=if(primary) accent else if(ivory) Color.parseColor("#EBE5D9") else Color.parseColor("#33312D")
        // Tint the background directly; keep ripple independent so every OEM
        // receives the same enabled/selected/pressed state, including reuse.
        button.stateListAnimator=null;button.background=rounded(Color.WHITE,14)
        button.backgroundTintList=ColorStateList(states,intArrayOf(if(ivory) Color.parseColor("#E5E0D8") else Color.parseColor("#292826"),accent,accent,accent,accent,fill))
        button.foreground=android.graphics.drawable.RippleDrawable(ColorStateList.valueOf(0x33FFFFFF),null,rounded(Color.WHITE,14))
        button.refreshDrawableState()
    }
    fun decorate(view: View=root) {
        if(view is FilmLoadingView) return // Mechanical scene owns its fixed neutral palette.
        when(view) {
            is CompoundButton -> { view.setTextColor(ink);view.textSize=14f;view.minHeight=dp(48);view.buttonTintList=ColorStateList.valueOf(accent) }
            is Button -> styleButton(view,view.tag == "primary")
            is EditText -> { view.setTextColor(ink);view.setHintTextColor(muted);view.textSize=15f
                val inset=if(view.tag=="labeled_field") 12 else 4;view.setPadding(dp(inset),dp(12),dp(inset),dp(12)) }
        }
        // Preserve the parent's actual LayoutParams type and every width/weight/margin.
        // Text in ordinary rows grows vertically instead of being cropped by a fixed dp height.
        if(view is TextView && view.text.isNotEmpty() && view.autoSizeTextType==TextView.AUTO_SIZE_TEXT_TYPE_NONE &&
            view.tag !in listOf("parameter_reset","scan_toolbar")) {
            val params=view.layoutParams as? LinearLayout.LayoutParams
            if(params!=null && params.height>0 && params.weight==0f) {
                view.minimumHeight=maxOf(view.minimumHeight,params.height)
                view.layoutParams=params.apply { height=ViewGroup.LayoutParams.WRAP_CONTENT }
            }
        }
        if(view is ViewGroup) for(i in 0 until view.childCount) decorate(view.getChildAt(i))
    }
    fun appearance(parent: LinearLayout,theme: String,language: String,change: (String,String)->Unit) {
        note(parent,"外观与语言仅影响界面；切换时保存当前配方草稿。","Appearance and language affect the interface; recipe drafts are saved when switching.")
        val themes=LinearLayout(activity)
        parent.addView(themes)
        for((key,zh,en) in listOf(Triple("dark","黑色","Dark"),Triple("ivory","米白","Light"))) {
            val button=action(themes,zh,en,key == theme) { change(key,language) }
            button.layoutParams=LinearLayout.LayoutParams(0,-2,1f);appearanceControls.add(button)
        }
        val languages=LinearLayout(activity);parent.addView(languages)
        for((key,label) in listOf("system" to copy("跟随系统","System"),"zh" to "中文","en" to "English")) {
            val button=action(languages,label,label,key == language) { change(theme,key) }
            button.layoutParams=LinearLayout.LayoutParams(0,-2,1f);appearanceControls.add(button)
        }
    }
    fun setBusy(active: Boolean) {
        if(selected==Page.SCAN) task.visibility=if(active) View.VISIBLE else View.GONE
        progress.visibility=if(active) View.VISIBLE else View.GONE
        pause.visibility=if(active) View.VISIBLE else View.GONE;pause.isEnabled=active
        appearanceControls.forEach { it.isEnabled=!active }
    }
    private fun rounded(color: String,radius: Int)=rounded(Color.parseColor(color),radius)
    private fun rounded(color: Int,radius: Int)=GradientDrawable().apply { setColor(color);cornerRadius=dp(radius).toFloat();setStroke(dp(1),line) }
}
