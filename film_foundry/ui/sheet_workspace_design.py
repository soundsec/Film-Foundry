"""Family-specific workspace presentation; no processing formulas or defaults."""
SECTIONS={
 'wet':(('片基与银像','Support & silver',('support','particle_reflectance','base_transmission','maximum_density','external_silver_load','physical_development_seconds')),
        ('倒膜与缺陷','Pour & defects',('pour_direction','flow_ridges','edge_pooling','pinholes','coating_variation','edge_loss','texture')),
        ('湿度与时机','Wetness & timing',('humidity','drying_seconds','exposure_delay_s','wetting_seconds','drying_lock_strength')),
        ('加银增强','Silver intensification',('enhancement_seconds','enhancement_nucleation'))),
 'cyanotype':(('纸与刷涂','Paper & brushing',('brush_direction','brush_overlap','coating_variation','edge_loss','texture','paper_reflectance')),
             ('颜料与水洗','Pigment & washing',('pigment_binding','paper_buffer','oxidation_seconds','alkali_susceptibility','humidity'))),
 'platinum':(('金属与影调','Metals & tone',('platinum_fraction','maximum_density','response_contrast','half_exposure')),
             ('纸与上药','Paper & coating',('coating_method','paper_sizing','texture','coating_variation','edge_loss','paper_reflectance')),
             ('湿度与清除','Humidity & clearing',('humidity','clearing_seconds','sensitizer_remaining'))),
 'paper':(('乳剂与纸基','Emulsion & paper',('kind','paper_reflectance','warmth','maximum_density')),
          ('表面与老化','Surface & aging',('coating_variation','edge_loss','texture','fog_level','sensitivity_remaining','wash_seconds')))}
V2_KEYS={'wet':('pour_direction','flow_ridges','edge_pooling','pinholes'),
         'cyanotype':('brush_direction','brush_overlap','pigment_binding','paper_buffer'),
         'platinum':('coating_method','paper_sizing'),'paper':()}
PAPER_KEYS=('paper_base','water_uptake_seconds','drying_seconds','surface_gloss','drydown')
for kind in ('paper','cyanotype','platinum'):
    SECTIONS[kind]+= (('纸基与湿干','Paper & moisture',PAPER_KEYS),)
for kind in SECTIONS:
    SECTIONS[kind]+=(('材料尺度','Material scale',('structure_model','material_long_edge_mm','texture_scale_mm','process_scale_mm')),)
WORKFLOWS={'wet':('prepare','sensitize','expose','develop','fix','wash','dry','varnish'),
           'cyanotype':('expose','wash','oxidize','dry'),'platinum':('expose','develop','clear','wash','dry'),
           'paper':('expose','develop','fix','wash','dry')}
STEP_NAMES={'wet':{'prepare':('倒膜','Pour'),'sensitize':('银浴','Silver bath'),'expose':('湿态曝光','Wet exposure'),'develop':('倾注显影','Pour developer'),'dry':('干燥','Dry'),'varnish':('上清漆','Varnish')},
 'cyanotype':{'expose':('接触曝光','Contact exposure'),'wash':('水洗成像','Wash out'),'oxidize':('氧化成蓝','Oxidize')},
 'platinum':{'expose':('接触曝光','Contact exposure'),'develop':('还原金属','Reduce metals'),'clear':('清除残铁','Clear iron'),'wash':('最终水洗','Final wash')},'paper':{}}
METRICS={'moisture':('湿润程度','Wetness'),'silver':('金属银','Metallic silver'),'halide':('残余银盐','Residual halide'),
 'blue':('蓝色颜料','Blue pigment'),'soluble_iron':('可溶铁盐','Soluble iron'),'unoxidized':('待氧化颜料','Unoxidized pigment'),
 'metal':('已形成金属','Formed metals'),'residual_iron':('残余铁','Residual iron'),'unreduced_metal':('未还原金属盐','Unreduced salts'),'dye':('染料','Dye')}
INSPECT={'collodion_access':('胶棉通透性','Collodion access'),'enhancement_silver':('剩余增强银','Remaining enhancement silver'),
 'enhancement_deposit':('增强沉积银','Deposited enhancement silver'),
 'external_silver':('剩余可溶银','Remaining soluble silver'),'external_deposit':('外源沉积银','Deposited external silver'),
 'paper_moisture':('纸面湿润分布','Paper moisture'),
 'coating':('涂层覆盖','Coating'),'moisture':('湿润分布','Wetness'),'flow':('流膜结构','Flow structure'),
 'blue_pigment':('蓝色颜料分布','Blue pigment'),'ferric':('未反应铁盐','Unreacted iron'),'binding':('颜料固着','Pigment binding'),
 'fibers':('纸纤维','Paper fibers'),'platinum':('铂分布','Platinum'),'palladium':('钯分布','Palladium'),'spent_ferric':('显影残铁','Spent iron'),
 'clear_access':('药浴可达性','Clearing access'),'silver':('银分布','Silver'),'dye':('染料分布','Dye')}


def next_step(kind,state,available,sequence):
    if not available:return None
    if state is None:return available[0]
    last={'coated':'prepare','sensitized':'sensitize','exposed':'expose','developed':'develop',
          'stopped':'develop','fixed':'fix','washed':'wash','cleared':'clear','oxidized':'oxidize','dry':'dry','varnished':'varnish'}.get(state.stage)
    if last=='fix' and 'bleach_fix' in sequence:last='bleach_fix'
    if last in sequence:
        for operation in sequence[sequence.index(last)+1:]:
            if operation in available:return operation
    return last if last in available else available[0]
