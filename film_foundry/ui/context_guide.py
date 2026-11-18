"""Contextual, offline guidance. Actions navigate; they never start processing."""
from film_foundry.ui.qt import QtWidgets
from film_foundry.ui.curve_editor import text
from film_foundry.ui.inline_editor import present_inline_editor


class ContextGuide(QtWidgets.QDialog):
    def __init__(self, title, lead, cards, actions=(), parent=None, snapshot=''):
        super().__init__(parent)
        self.setWindowTitle(text('本页指南 · ', 'Page guide · ')+title);self.resize(620, 580)
        layout=QtWidgets.QVBoxLayout(self)
        heading=QtWidgets.QLabel(title);heading.setObjectName('pageTitle');layout.addWidget(heading)
        note=QtWidgets.QLabel(lead);note.setWordWrap(True);layout.addWidget(note)
        self.snapshot=QtWidgets.QLabel(snapshot);self.snapshot.setWordWrap(True)
        self.snapshot.setObjectName('sectionNote');self.snapshot.setVisible(bool(snapshot));layout.addWidget(self.snapshot)
        scroll=QtWidgets.QScrollArea();scroll.setWidgetResizable(True);layout.addWidget(scroll,1)
        body=QtWidgets.QWidget();content=QtWidgets.QVBoxLayout(body);scroll.setWidget(body)
        self.card_titles=[]
        for name,description in cards:
            self.card_titles.append(name)
            card=QtWidgets.QGroupBox(name);box=QtWidgets.QVBoxLayout(card)
            label=QtWidgets.QLabel(description);label.setWordWrap(True);box.addWidget(label);content.addWidget(card)
        content.addStretch()
        self.action_buttons={};self.callback=None
        for key,label,callback in actions:
            button=QtWidgets.QPushButton(label)
            button.clicked.connect(lambda checked=False,cb=callback:self.choose(cb))
            layout.addWidget(button);self.action_buttons[key]=button
        close=QtWidgets.QPushButton(text('返回当前工作', 'Back to work'));close.clicked.connect(self.reject);layout.addWidget(close)

    def choose(self, callback):
        self.callback=callback;self.accept()


def show_guide(owner, title, lead, cards, actions=(),snapshot=''):
    dialog=ContextGuide(title,lead,cards,actions,owner,snapshot)
    present_inline_editor(owner,dialog,lambda:dialog.callback() if dialog.callback else None)
    return dialog


MATERIAL_GUIDES={
    'peel': ('撕拉片', 'Peel-apart',
        '曝光 → 铺药与接触 → 推进时间 → 撕开。接触中可分段推进；撕开立即停止转移。',
        'Expose → spread and contact → advance time → separate. Advance in intervals; separation stops transfer.',
        '比较早揭与晚揭：分别从同一来源重新开始，只改变推进时间，再比较接收片。残片只有材料声明为黑白负片并完成清理后，才可用于透射印相。',
        'Compare early and late separation using separate starts from the same source, changing only duration. A donor is a transmission negative only when the material declares one and it has been cleared.'),
    'integral': ('即显片', 'Integral instant',
        '曝光 → 出片 → 铺药 → 推进显影与中和。标准步骤使用当前设置；药剂或中和不足时，仍可能处于反应中。',
        'Expose → eject → spread → advance development and neutralization. Reference steps use current settings; insufficient reagent or neutralization can leave the print reacting.',
        '体验分段环境：铺药后设置后续环境，再推进时间。已有反应不会重算；“保留当前成片”是明确结束当前过程，不会替你补足中和。',
        'Try segmented conditions: set the next environment after spreading, then advance. Earlier reactions remain unchanged. Keep current print explicitly ends processing without supplying missing neutralization.'),
    'plate': ('银版', 'Silver plate',
        '备版 → 抛光 → 感光化 → 曝光 → 显影 → 定影。定影未完成可继续本浴；镀金在可选操作中，先载入金浴。',
        'Prepare → polish → sensitize → expose → develop → fix. Continue an incomplete bath; optional gilding requires loading a gold bath first.',
        '先体验角度观察：形成后只改观看倾角、光源倾角和背景，观察银像与金属底的反差变化。这些观看调整不会重新形成颗粒。',
        'Explore angle-dependent viewing: after formation, change view tilt, light tilt and background. These observation controls never reform particles.'),
    'wet': ('湿版', 'Wet collodion',
        '备版与敏化 → 趁湿曝光 → 显影 → 定影 → 水洗。湿润、等待和药浴都有先后条件；以当前可执行步骤为准。',
        'Prepare and sensitize → expose while wet → develop → fix → wash. Wetness, waiting and baths have prerequisites; follow the available steps.',
        '先走标准流程，再从相同来源比较曝光前等待。负片用透射观看，玻璃正片用深色背衬观察；选择不同观看方式不会改变已经形成的银。',
        'Run a reference process, then compare pre-exposure waiting from the same source. View negatives in transmission and glass positives against a dark backing; observation retains the formed silver.'),
    'paper': ('相纸', 'Photographic paper',
        '曝光 → 显影 → 定影（彩色相纸为漂定）→ 水洗。标准流程与单步操作共用当前参数。',
        'Expose → develop → fix (bleach-fix for color paper) → wash. Reference and individual steps share current settings.',
        '普通负感相纸直接印正片会反相。可先在胶片工作台形成底片，再从“光学来源”接触印相；直接正像相纸使用自己的极性规则。',
        'A positive image produces an inverted image on negative-working paper. Form a film negative first, then use Optical source for contact printing. Direct-positive paper follows its own polarity.'),
    'cyanotype': ('蓝晒', 'Cyanotype',
        '曝光 → 水洗 → 氧化。直接输入普通正片会得到明暗反转；软件不会替材料反相。',
        'Expose → wash → oxidize. A positive input gives inverted tones; no automatic reversal is added.',
        '体验底片接触印相：在胶片结果中保留底片母版，再到本页载入光学来源。水洗与氧化属于形成；观看曝光只改变观察。',
        'Try contact printing from a saved film negative through Optical source. Washing and oxidation form the material; viewing exposure only changes observation.'),
    'platinum': ('铂钯', 'Platinum / palladium',
        '曝光 → 显影 → 清除 → 水洗。负感印相承接负片来源，直接使用正片会反相。',
        'Expose → develop → clear → wash. Negative-working printing expects a negative; positive inputs invert.',
        '先用标准流程得到介质，再分别尝试铂钯比例或清除条件。材料配比须从新片开始；观看与导出不继续清除或水洗。',
        'Start with a reference process, then compare metal ratios or clearing conditions. Material ratios require a fresh start; viewing and export never continue clearing or washing.'),
}


def open_material_guide(window):
    zh,en,flow_zh,flow_en,try_zh,try_en=MATERIAL_GUIDES[window.kind]
    session=window.session
    if window.busy:lead=text('当前操作正在进行；可先阅读，完成后继续。','An operation is running. Read now and continue when it finishes.')
    elif session.group is not None:lead=text('当前已有可观察介质。观看只读；可选处理仍以材料允许的阶段为准。','A viewable medium is available. Viewing is read-only; further processing follows the material’s allowed stages.')
    elif session.state is not None:lead=text('材料已经开始形成；材料定义已冻结，后续操作只影响之后的过程。','Formation has started. Material definitions are frozen; later actions affect only subsequent processing.')
    elif session.image is not None:lead=text('来源已就绪。先查看工序参数，再执行标准步骤或逐步操作。','Source ready. Review step settings, then run reference steps or work step by step.')
    else:lead=text('先打开图像或光学来源，再开始这张材料。','Open an image or optical source to start this material.')
    cards=[(text('工艺顺序','Process order'),text(flow_zh,flow_en)),
           (text('试条比较','Compare with test strips'),text('图像工具栏的试条只在副本上计算。新片按当前标准步骤；已开始的材料只试选中可执行的一步或只读观看。采用只回填参数，可撤回；不会推进当前材料。已有材料保持形成尺寸计算，仅缩小显示。',
                'Image-toolbar trials use a separate session. Fresh sources follow the reference steps; existing material trials perform one selected available step or read-only viewing. Adoption only changes settings and supports Undo. Existing formation dimensions are retained; only the display is reduced.')),
           (text('预览历史与快捷键','Preview history and shortcuts'),text('历史对照最近成功预览，选择只看图，恢复参数可撤回。材料推进后旧图仅供对照，不能倒退工序；回到工序请用撤回或材料存档。默认按住空格看原图，松开回预览。全局设置 → 快捷键可查看和修改键位。历史不进自动保险。',
                'History compares successful previews. Selection only compares; restoring settings supports Undo. After material advances, old images are comparison-only; use Undo or an archive for earlier states. Hold Space by default for source, release for preview. Global settings → Shortcuts lists and configures keys. History is excluded from recovery.')),
           (text('可以先试什么','Try this first'),text(try_zh,try_en)),
           (text('保存与继续','Save and continue'),text('保存材料状态用于继续允许的工序；保存最终介质用于观看、扫描或级联。导出图像才得到普通图片。关闭指南不会更改参数或推进反应。',
                'Save state to continue permitted operations. Save final media for viewing, scanning or optical printing. Export an image for a regular picture. Closing this guide never changes parameters or advances reactions.')),
           (text('上一次工作保险','Last-work insurance'),text('主窗口每5秒检查工作参数。保险默认保留1份，可选3或5份。异常退出后先在图库恢复或忽略旧记录。文件菜单可恢复参数与来源引用，未保存的工序需重新执行。',
                'The main window checks settings every 5 seconds. Keep one recovery record by default or choose three or five. After an interrupted exit, recover or dismiss the previous work in the library. File restores settings and source references. Rerun unsaved steps.'))]
    actions=[]
    if not window.busy:
        if session.operations:
            operation=session.operations[0]
            callback=(lambda:window.workflow.select_flow((operation,))) if hasattr(window,'workflow') else (lambda:window._select_step(operation))
            actions.append(('step',text('查看当前可执行步骤','View an available step'),callback))
        if session.state is not None or session.group is not None:
            actions.append(('history',text('查看工艺记录','View process history'),lambda:window.inspector.history_button.setChecked(True)))
    from pathlib import Path
    stage=text('尚未形成','Not formed')
    if session.state is not None:
        if window.kind in ('peel','integral','plate'):
            from film_foundry.ui.material_lab_window import STAGES
            stage=text(STAGES.get(session.state.stage,session.state.stage),session.state.stage)
        else:
            from film_foundry.ui.sheet_lab_window import STAGES
            stage=text(*STAGES.get(session.state.stage,(session.state.stage,session.state.stage)))
        stage+=f' · {session.state.elapsed_seconds:g} s'
    elif session.group is not None:stage=text('已形成介质','Formed medium')
    snapshot=text('当前工作 · 来源：','Current work · Source: ')+(Path(session.source_name).name or text('未选择','Not selected'))+' ｜ '+stage
    if hasattr(window,'workflow'):snapshot+='\n'+window.workflow.progress_note.text()
    return show_guide(window,text(zh,en),lead,cards,actions,snapshot)


def install_material_guide(window, layout):
    button=QtWidgets.QPushButton(text('? 本页指南','? Page guide'))
    button.clicked.connect(lambda:open_material_guide(window));layout.addWidget(button);window.guide_button=button


def open_desktop_guide(panel):
    page=panel.workspace_host.currentWidget()
    if getattr(page,'kind',None) in MATERIAL_GUIDES and hasattr(page,'session'):
        return open_material_guide(page)
    actions=[];snapshot=''
    if page is panel.film_workspace:
        mode=panel.pipeline_mode.get()
        from pathlib import Path
        source=panel.negative_path.get() if mode in ('scan','develop_latent') else panel.input_path.get()
        stages=[panel.pipeline_bar.buttons[key].text().split('\n')[0] for key,state in panel.pipeline_bar.states.items() if state in ('editable','observe')]
        snapshot=text('当前工作 · 来源：','Current work · Source: ')+(Path(source).name if source else text('未选择','Not selected'))
        snapshot+=text(' ｜ 可调：',' ｜ Editable: ')+' / '.join(stages)
        try:
            config=panel._config()
            from film_foundry.core.execution import processing_long_edge
            limit=config.output.preview_long_edge if panel.run_as_preview.get() else processing_long_edge(config)
            size=text('沿用来源介质尺寸','Source medium size') if mode=='scan' else text(f'长边上限 {limit} px',f'Long edge up to {limit} px') if limit else text('原尺寸','Original size')
            quality={'draft':text('低质量','Low quality'),'standard':text('中质量','Medium quality'),'high':text('高质量','High quality')}.get(config.processing.quality_mode,config.processing.quality_mode)
            snapshot+=text('\n正式输出：','\nFormal output: ')+size+' · '+quality
        except (ValueError,TypeError):snapshot+=text('\n输出设置尚有无效值，请先检查。','\nOutput settings contain invalid values; review them first.')
        snapshot+='\n'+panel.preview_state_label.text()
        if panel.result_view.source is not None and panel._preview_state_kind in ('ready','stale'):
            snapshot+=text(f' · 屏幕快速草稿 {panel.result_view.source.width()}×{panel.result_view.source.height()}（长边上限 {panel.PREVIEW_EDGE} px）；正式执行按输出设置重算。',f' · Fast display draft {panel.result_view.source.width()}×{panel.result_view.source.height()} (limit {panel.PREVIEW_EDGE} px); execution recomputes using output settings.')
        title=text('胶片工作','Film work')
        lead=panel.workflow_labels[mode]
        cards=[(text('先看阶段，再调参数','Follow the stages'),text('阶段卡片主体进入参数页或查看冻结记录；右侧“设为终点”选择执行终点，正式处理仍由底部按钮启动。副标题显示预设来源或已保存的形成记录。冲洗页使用常用 / 按效果 / 按药浴；扫描页独立显示进阶参数。',
            'Click a stage card for controls or frozen records; its Set endpoint button chooses the endpoint. The bottom action starts formal processing. Subtitles identify preset sources or saved formation records. Development uses Common / By effect / By bath; scan advanced controls are independent.'))]
        if mode=='scan':
            cards.append((text('一底多扫','Several scans from one medium'),text('调整光源、影调与颜色只读取来源。正式执行另存成片；若想改变冲洗，需要从有记录的原潜影另做冲洗，不能从成片反推。',
                'Lighting, tone and color only read the source. Save another scan when ready. To change development, use a recorded original latent; never reconstruct exposure from a final image.')))
        elif mode=='develop_latent':
            cards.append((text('一源多冲','Several developments from one latent'),text('此来源的材料和曝光已经冻结。可更换冲洗条件，执行后另存底片；原潜影和已有结果保留。',
                'Material and exposure are frozen in this source. Adjust processing and save another medium; the original latent and prior results remain.')))
        else:
            cards.append((text('先选终点','Choose the endpoint'),text('点击曝光、冲洗或扫描卡片右侧的“设为终点”，决定保存潜影、底片或扫描成片；底部主按钮才执行。扫描终点旁的下拉菜单可选择仅扫描原图。整卷仍在本次执行中选择。',
                'Use Set endpoint on exposure, development or scan to select a latent, medium or scanned image; the bottom action executes. The scan endpoint menu also offers scanning the original only. Rolls retain the Task selector.')))
        cards.append((text('改动会重算哪里','What changes recalculate'),text('分组标题标出重算范围。照片改冲洗会重做曝光和冲洗；已有潜影只重做冲洗。仅观察不改变介质，但首次预览、清缓存或形成参数变化后仍需先形成。悬停标识可看说明。',
            'Group headings identify recalculation scope. Development changes re-expose photos, but redevelop saved latents. Observation retains the medium; a first preview, cleared cache or changed formation settings may still need formation. Hover over a label for details.')))
        cards.append((text('预览与保存工作','Preview and saved work'),text('预览用于查看当前参数，正式执行才保存结果。“保存工作”保留工作来源与配置，不能替代成片输出。观察调整不会改变底片。',
            'Preview checks current settings; formal execution saves results. Save work retains sources and configuration, and does not replace image output. Observation retains the medium.')))
        if panel._active_roll is None:
            cards.append((text('历史对照与快捷键','History and shortcuts'),text('历史左侧选旧图，右侧是最近成功预览；恢复选中参数才改工作，可撤回。每页最多6条，历史图共用32 MiB，不进存档或自动保险。默认按住空格看原图，松开回预览；全局设置 → 快捷键可查看和修改键位。',
                'History compares an older preview on the left with the latest successful one on the right. Restore explicitly, with Undo available. Up to 6 entries per page share a 32 MiB image budget; history is excluded from archives and recovery. Hold Space by default for source, release for preview. Global settings → Shortcuts lists and configures keys.')))
            cards.append((text('用试条比较参数','Compare settings with test strips'),text('预览操作栏的“试条”按同一来源和随机身份计算 2–9 个数值。沿当前处理设置缩小图像，点选放大并与当前值参照对比；单浴参照与各条使用同一药液定义。采用才改配方，可撤回。停止在当前一条结束后生效。',
                'Test strip compares 2–9 values on one source with a shared random identity. It uses current processing settings at a smaller size. Compare with the current-value reference; bath references share the trials’ solution definition. Adopt explicitly, with Undo available. Stop takes effect after the current trial.')))
            key='scan' if mode=='scan' else 'develop' if mode in ('develop','develop_latent') else 'film'
            actions.append(('controls',text('回到本次参数','Open task controls'),lambda:panel.navigation.setCurrentRow(panel.page_index[key])))
    elif page is getattr(panel,'library_view',None) or page.__class__.__name__ in ('LibraryBrowser','ResultStackView'):
        title=text('图库与来源链','Library and sources');lead=text('先浏览，再选择要创建的工作。','Browse first, then choose the work to create.')
        cards=[(text('导入与筛选','Import and filters'),text('空图库可导入照片、打开底片/潜影或进入整卷暗房。拖入本地图片/材料登记来源，拖入文件夹后台索引图片，不搬动原文件或自动处理。详细条件在“筛选”展开；无匹配时可清除。','Start with photos, saved media/latents or whole-roll darkroom. Drop local images/materials to import references, or folders to index images in the background. Files stay in place and no processing starts. Expand Filters for details; clear them when no items match.')),
               (text('原图、潜影、底片、成片','Photo, latent, medium, scan'),text('原图用于新曝光；潜影可反复冲洗；底片可多次扫描或接触印相；成片用于查看与分享。下游不会还原丢失的上游。','Photos start exposures; latents support repeated development; media support scans and optical printing; scans are regular pictures. Downstream images cannot recover missing sources.')),
               (text('创建与浏览','Create and browse'),text('双击先只读浏览。创建工作时选材料；查看已有底片时，可选择扫描或光学印相。切换工作页会保留当前草稿。','Double-click to browse read-only. Choose a material when creating work; use saved media for scanning or optical printing. Switching pages preserves drafts.')),
               (text('关联结果','Related results'),text('按来源查看衍生结果，区分同一底片的不同扫描与同一潜影的不同冲洗。请按实际可用入口继续。','Group derivatives by source, distinguishing scans of one medium from developments of one latent. Continue through the available actions.'))]
    elif page is getattr(panel,'roll_view',None):
        title=text('整卷暗房','Roll darkroom');lead=text('胶卷按装填、曝光、封卷、冲洗与扫描推进。','Load, expose, seal, develop and scan a roll.')
        cards=[(text('装卷后冻结','Frozen on loading'),text('材料、规格与片张身份随胶卷保存。已拍格子不能通过更改新配置重新曝光。','Material, format and frame identity belong to the roll. New settings do not re-expose captured frames.')),
               (text('作弊预览','Cheat preview'),text('未开始冲洗时，冲洗配方页可按未提交草稿试算第一格已曝光潜影。预览窗口阻止调参与切换工作，关闭即丢弃结果；没有刷新、采用或导出，不推进胶卷。',
                    'Before development starts, the bath editor can forecast the first exposed frame using its uncommitted draft. The preview blocks editing and switching work; closing discards it. No refresh, adoption or export, and the roll remains unchanged.')),
               (text('准备与执行','Prepare and execute'),text('先查看本卷状态和冲洗/扫描配置，再使用页内可用操作。已经开始的工序使用冻结配方，扫描只观察形成的底片。','Review roll state and processing settings before using its available actions. Started processing uses its frozen recipe; scanning only observes formed media.'))]
    else:
        title=panel.workspace_host.tabText(panel.workspace_host.currentIndex()) or text('当前工作','Current work')
        lead=text('当前页的操作只作用于所选工作。','Actions apply to the selected work.')
        cards=[(text('草稿与应用','Drafts and applying'),text('先查看当前来源与作用范围。配置编辑后显式应用；只保存预设不会自动改变当前材料。切换页面保留已有工作。','Check the source and scope. Explicitly apply edited configuration; saving a preset alone does not change material. Switching pages retains work.')),
               (text('过程与观察','Processing and viewing'),text('按本页允许的操作继续。已经完成的反应保持冻结，后续步骤影响之后的过程；预览和观察不会替代工序执行。','Continue through the actions this page allows. Completed reactions stay frozen; later steps affect subsequent processing. Preview and observation never substitute for executing a process.')),
               (text('保留结果','Retain results'),text('保存工作、材料存档与导出图像各有用途。需要以后继续时保留工作或中间状态；需要分享时另行导出成片。','Saved work, material archives and exported images serve different purposes. Keep work or intermediate states to continue later; export images to share.'))]
    return show_guide(page,title,lead,cards,actions,snapshot)

