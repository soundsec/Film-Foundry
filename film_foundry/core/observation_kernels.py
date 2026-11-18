"""Pointwise native optics. Each material retains its own optical equations."""

COMMON = r'''
#define F(k) src[offsets[k]*count+p]
#define V(k,c) src[offsets[k]*count+p*3+(c)]
#define A(k,c) src[offsets[k]*count+p*channels+(channels==1?0:(c))]
'''
PAPER_SURFACE = r'''
 float wet=F(WET),base=F(BASE);
 float exponent=1.f+params[3]*(1.f-wet);
 float relative=base>0.f?fminf(fmaxf(value/base,0.f),1.f):0.f;
 value=base*powf(relative,exponent);
 float gloss=params[4]+(1.f-params[4])*.65f*wet;
 float weight=.035f*gloss;
 float rough=fminf(fmaxf(.15f+.65f*(1.f-gloss)+params[2]*.08f,0.f),1.f);
 float shine=powf(params[5],8.f+120.f*(1.f-rough))*params[6]*params[7];
 value=value*(1.f-weight)+weight*shine;
'''
BODIES={
'observe_peel':r'''
 for(int c=0;c<3;c++) {
   float amount=A(0,c)+(kind==1?A(1,c):0.f);
   float density=amount*F(2)+V(4,c);
   dst[p*3+c]=F(3)*powf(10.f,-density);
 }
''',
'observe_instant':r'''
 float opacity=F(6),uncoated=fminf(fmaxf(1.f-F(4)/.12f,0.f),1.f);
 float backing_tint[3]={.14f,.085f,.045f};
 for(int c=0;c<3;c++) {
   float density=A(0,c)*F(1)+V(3,c);
   float image=F(2)*powf(10.f,-density)*(float)params[c]*(float)params[3];
   double backing=(double)backing_tint[c]*params[c]*params[3];
   backing*=.55f+.9f*F(5);
   double mixed=image*(1.f-uncoated)+backing*uncoated;
   float veil=(float)params[c]*.12f*(float)params[3];
   dst[p*3+c]=(float)(mixed*(1.f-opacity)+veil*opacity);
 }
''',
'observe_paper':r'''
 float silver=((V(0,0)+V(0,1))+V(0,2))/3.f*params[0];
 float salt=((V(1,0)+V(1,1))+V(1,2))/3.f*.08f;
 float tint[3]={1.f,1.f+.12f*params[1],1.f+.35f*params[1]};
 float matrix[9]={1.f,.06f,.03f,.04f,1.f,.04f,.02f,.08f,1.f};
 for(int c=0;c<3;c++) {
   float density=silver*tint[c]+salt+V(2,c);
   if(kind) density+=((V(3,0)*matrix[c]+V(3,1)*matrix[3+c])+V(3,2)*matrix[6+c])*params[0];
   float value=F(4)*powf(10.f,-density);
   SURFACE
   dst[p*3+c]=value;
 }
''',
'observe_iron':r'''
 float iron=F(0)+F(1)+(kind?F(2):0.f);
 float t[3]={1.35f,.85f,.12f},reduced[3]={.15f,.1f,.03f};
 float pd[3]={.98f,1.f,1.045f},stain[3]={.04f,.16f,.65f};
 float scatter=1.f+params[2]*.35f*F(7);
 for(int c=0;c<3;c++) {
   float amount=kind?F(3)+F(4)*pd[c]:F(3);
   float absorption=.5f*expm1f(amount*params[0]*params[1]);
   if(!kind)absorption=absorption*t[c]+F(4)*reduced[c];
   absorption+=iron*stain[c]+V(5,c);
   float k=fmaxf(absorption,0.f)/fmaxf(scatter,1.e-4f);
   float value=F(6)/(1.f+k+sqrtf(k*(k+2.f)));
   SURFACE
   dst[p*3+c]=value;
 }
''',
'observe_wet':r'''
 float silver=F(0)+F(1)+F(2),salt=F(3)+F(4)+F(5);
 float varnish[3]={.002f,.006f,.018f};
 float tint[3]={kind==1?1.f:.95f,kind==1?.96f:.98f,kind==1?.9f:1.f};
 float scatter=(1.f-expf(-silver*2.f))*params[1]*F(7);
 float rough=fminf(fmaxf(F(9)+.08f,0.f),1.f);
 float glare=((kind==1?.035f:.012f)+.025f*F(8))*powf(params[3],8.f+120.f*(1.f-rough))*params[4]*params[5];
 for(int c=0;c<3;c++) {
   float v=F(8)*varnish[c],residue=V(6,c);
   float density=(silver*params[0]+salt*.12f)+residue+v;
   dst[p*3+c]=kind==0?params[2]*powf(10.f,-density):
     (kind==1?.006f:.022f)*powf(10.f,-2.f*density)+scatter*tint[c]*powf(10.f,-residue-v)+glare;
 }
''',
'observe_plate':r'''
 float nx=(F(0)-.5f)*.10f,ny=(F(1)-.5f)*.10f;
 float norm=sqrtf((nx*nx+ny*ny)+1.f);nx/=norm;ny/=norm;float nz=1.f/norm;
 double nl=fmax((nx*params[0]+ny*params[1])+nz*params[2],0.0);
 double alignment=fmin(fmax(((2*nl*nx-params[0])*params[3]+(2*nl*ny-params[1])*params[4])+(2*nl*nz-params[2])*params[5],0.0),1.0);
 float rough=fminf(fmaxf((F(2)+F(3)*.12f)+F(4)*.16f,.02f),1.f);
 float power=fminf(2.f/(rough*rough)-2.f,5000.f);
 double specular=pow(alignment,(double)power)*nl;
 double tint[3]={.02,.08,.22},salt_abs[3]={.15,.25,.7};
 double wavelength[3]={650.,535.,460.},absorb[3]={1.1,1.35,1.7};
 for(int c=0;c<3;c++) {
   double light=params[6+c];
   double metal=(params[9+c]+light*specular)*F(6)*(1-F(5)*tint[c]);
   double particles=light*nl*F(7)*(1.f-F(5)*.08f);
   particles*=.96f+.08f*F(8);
   metal=metal*(1.f-.65f*F(3))+light*(.018f*F(3)+.012f*F(4));
   double value=metal*(1.f-F(9))+particles*F(9);
   value=value*exp(-F(10)*salt_abs[c])+light*.08*(-expm1f(-2.f*F(10)));
   float thickness=F(11),strength=-expm1f(-thickness*7.f);
   float phase=(float)(4*3.141592653589793*1.8)*(thickness*320.f)*(float)fmax(params[5],.1);
   double interference=1-(.62f*strength)*(.5+.5*cos(phase/wavelength[c]));
   value*=interference*exp(-thickness*absorb[c]);
   dst[p*3+c]=(float)(value*params[12]);
 }
''',
'encode_srgb':r'''
 for(int c=0;c<3;c++) {
   float v=V(0,c);
   if(isnan(v)){dst[p*3+c]=v;continue;}
   v=fminf(fmaxf(v,0.f),1.f);
   dst[p*3+c]=v<=.0031308f?v*12.92f:(powf(v,(float)(1.0/2.4))*1.055f)-.055f;
 }
'''}


def body(name):
    code=BODIES[name]
    if 'SURFACE' in code:
        code=code.replace('SURFACE',PAPER_SURFACE.replace('WET','5' if name=='observe_paper' else '8').replace('BASE','4' if name=='observe_paper' else '6'))
    precision='double' if name in ('observe_plate','observe_instant') else 'float'
    return COMMON+f'''KERNEL void {name}(GLOBAL const float* src, GLOBAL const int* offsets,
 GLOBAL const {precision}* params,int kind,int channels,int count,GLOBAL float* dst) {{
 int p=PIXEL;if(p>=count)return;
 {code}
 }}'''
