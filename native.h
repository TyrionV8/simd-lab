/* Helpers emitidos pelo SIMD Lab. Vetores são pequenos; isto não é um benchmark do Colibri. */
#include <immintrin.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <math.h>
#define TARGET(features) __attribute__((target(features)))

static int integer_at(const void *p, int kind, int i) {
    if (kind == 0) return ((const uint8_t *)p)[i];
    if (kind == 1) return ((const int8_t *)p)[i];
    if (kind == 2) return ((const int16_t *)p)[i];
    return ((const int32_t *)p)[i];
}
static TARGET("sse2") void split_sse(const uint8_t *p,uint8_t *y,int high) {
    for(int i=0;i<32;i+=16){__m128i v=_mm_loadu_si128((const __m128i *)(p+i));if(high)v=_mm_srli_epi16(v,4);_mm_storeu_si128((__m128i *)(y+i),_mm_and_si128(v,_mm_set1_epi8(15)));}
}
static TARGET("avx2") void split_avx2(const uint8_t *p,uint8_t *y,int high) {
    __m256i v=_mm256_loadu_si256((const __m256i *)p);if(high)v=_mm256_srli_epi16(v,4);_mm256_storeu_si256((__m256i *)y,_mm256_and_si256(v,_mm256_set1_epi8(15)));
}
static TARGET("ssse3") void maddubs_ssse3(const uint8_t *u,const int8_t *x,int16_t *y) {
    for(int i=0;i<64;i+=16)_mm_storeu_si128((__m128i *)(y+i/2),_mm_maddubs_epi16(_mm_loadu_si128((const __m128i *)(u+i)),_mm_loadu_si128((const __m128i *)(x+i))));
}
static TARGET("avx2") void maddubs_avx2(const uint8_t *u,const int8_t *x,int16_t *y) {
    for(int i=0;i<64;i+=32)_mm256_storeu_si256((__m256i *)(y+i/2),_mm256_maddubs_epi16(_mm256_loadu_si256((const __m256i *)(u+i)),_mm256_loadu_si256((const __m256i *)(x+i))));
}
static TARGET("sse2") void maddwd_sse(const int16_t *p,int32_t *y) {
    for(int i=0;i<32;i+=8)_mm_storeu_si128((__m128i *)(y+i/2),_mm_madd_epi16(_mm_loadu_si128((const __m128i *)(p+i)),_mm_set1_epi16(1)));
}
static TARGET("avx2") void maddwd_avx2(const int16_t *p,int32_t *y) {
    for(int i=0;i<32;i+=16)_mm256_storeu_si256((__m256i *)(y+i/2),_mm256_madd_epi16(_mm256_loadu_si256((const __m256i *)(p+i)),_mm256_set1_epi16(1)));
}
static TARGET("sse2") void merge_sse(const int32_t *p,int32_t *y) {
    for(int i=0;i<8;i+=4)_mm_storeu_si128((__m128i *)(y+i),_mm_add_epi32(_mm_loadu_si128((const __m128i *)(p+i)),_mm_loadu_si128((const __m128i *)(p+i+8))));
}
static TARGET("avx2") void merge_avx2(const int32_t *p,int32_t *y) {
    _mm256_storeu_si256((__m256i *)y,_mm256_add_epi32(_mm256_loadu_si256((const __m256i *)p),_mm256_loadu_si256((const __m256i *)(p+8))));
}
static TARGET("sse4.1") __m128i four_integers(const void *p, int kind, int i) {
    int32_t b; int64_t h;
    if (kind < 2) {
        memcpy(&b, (const char *)p+i, 4);
        __m128i v = _mm_cvtsi32_si128(b);
        return kind == 0 ? _mm_cvtepu8_epi32(v) : _mm_cvtepi8_epi32(v);
    }
    if (kind == 2) { memcpy(&h, (const int16_t *)p+i, 8); return _mm_cvtepi16_epi32(_mm_cvtsi64_si128(h)); }
    return _mm_loadu_si128((const __m128i *)((const int32_t *)p+i));
}
static TARGET("sse4.1") void to_float_sse(const void *p, int kind, float *y, int n) {
    int i = 0;
    for (; i+4<=n; i+=4) _mm_storeu_ps(y+i, _mm_cvtepi32_ps(four_integers(p, kind, i)));
    for (; i<n; i++) y[i] = (float)integer_at(p, kind, i);
}
static TARGET("avx,sse4.1") void to_float_avx(const void *p, int kind, float *y, int n) {
    int i = 0;
    for (; i+8<=n; i+=8) {
        __m128 a = _mm_cvtepi32_ps(four_integers(p, kind, i));
        __m128 b = _mm_cvtepi32_ps(four_integers(p, kind, i+4));
        _mm256_storeu_ps(y+i, _mm256_insertf128_ps(_mm256_castps128_ps256(a), b, 1));
    }
    for (; i<n; i++) y[i] = (float)integer_at(p, kind, i);
}
static TARGET("avx2") void to_float_avx2(const void *p, int kind, float *y, int n) {
    int i = 0;
    for (; i+8<=n; i+=8) {
        __m256i v;
        if (kind < 2) {
            int64_t b; memcpy(&b, (const char *)p+i, 8); __m128i q = _mm_cvtsi64_si128(b);
            v = kind == 0 ? _mm256_cvtepu8_epi32(q) : _mm256_cvtepi8_epi32(q);
        } else if (kind == 2) v = _mm256_cvtepi16_epi32(_mm_loadu_si128((const __m128i *)((const int16_t *)p+i)));
        else v = _mm256_loadu_si256((const __m256i *)((const int32_t *)p+i));
        _mm256_storeu_ps(y+i, _mm256_cvtepi32_ps(v));
    }
    for (; i<n; i++) y[i] = (float)integer_at(p, kind, i);
}
static TARGET("avx,f16c,sse4.1") void decode_f16c(const uint8_t *u, float *y, int n) {
    int i = 0;
    for (; i+8<=n; i+=8) {
        __m128i b = _mm_loadl_epi64((const __m128i *)(u+i));
        __m128i bits = _mm_or_si128(_mm_cvtepu8_epi16(b), _mm_set1_epi16(0x6400));
        _mm256_storeu_ps(y+i, _mm256_sub_ps(_mm256_cvtph_ps(bits), _mm256_set1_ps(1032.f)));
    }
    for (; i<n; i++) y[i] = (float)u[i]-8.f;
}
static TARGET("avx,sse4.1") void decode_bits32(const uint8_t *u, float *y, int n) {
    int i = 0;
    for (; i+8<=n; i+=8) {
        __m128i b = _mm_loadl_epi64((const __m128i *)(u+i));
        __m128 lo = _mm_castsi128_ps(_mm_cvtepu8_epi32(b));
        __m128 hi = _mm_castsi128_ps(_mm_cvtepu8_epi32(_mm_srli_si128(b, 4)));
        __m256 v = _mm256_insertf128_ps(_mm256_castps128_ps256(lo), hi, 1);
        v = _mm256_or_ps(v, _mm256_set1_ps(8388608.f));
        _mm256_storeu_ps(y+i, _mm256_sub_ps(v, _mm256_set1_ps(8388616.f)));
    }
    for (; i<n; i++) y[i] = (float)u[i]-8.f;
}
static void binary_scalar(const float *a, int na, const float *b, int nb, float *y, int n, int add) {
    for (int i=0; i<n; i++) y[i] = add ? a[na==1?0:i]+b[nb==1?0:i] : a[na==1?0:i]*b[nb==1?0:i];
}
static TARGET("sse2") void binary_sse(const float *a, int na, const float *b, int nb, float *y, int n, int add) {
    int i=0;
    for (; i+4<=n; i+=4) {
        __m128 av = na==1 ? _mm_set1_ps(a[0]) : _mm_loadu_ps(a+i);
        __m128 bv = nb==1 ? _mm_set1_ps(b[0]) : _mm_loadu_ps(b+i);
        _mm_storeu_ps(y+i, add ? _mm_add_ps(av,bv) : _mm_mul_ps(av,bv));
    }
    for (; i<n; i++) y[i] = add ? a[na==1?0:i]+b[nb==1?0:i] : a[na==1?0:i]*b[nb==1?0:i];
}
static TARGET("avx") void binary_avx(const float *a, int na, const float *b, int nb, float *y, int n, int add) {
    int i=0;
    for (; i+8<=n; i+=8) {
        __m256 av = na==1 ? _mm256_set1_ps(a[0]) : _mm256_loadu_ps(a+i);
        __m256 bv = nb==1 ? _mm256_set1_ps(b[0]) : _mm256_loadu_ps(b+i);
        _mm256_storeu_ps(y+i, add ? _mm256_add_ps(av,bv) : _mm256_mul_ps(av,bv));
    }
    for (; i<n; i++) y[i] = add ? a[na==1?0:i]+b[nb==1?0:i] : a[na==1?0:i]*b[nb==1?0:i];
}
static void quantize(const float *x, int8_t *q, float *scale, int32_t *sums, int n, int group) {
    for (int g=0; g<n/group; g++) {
        float am=0.f; for (int i=0; i<group; i++) am=fmaxf(am,fabsf(x[g*group+i]));
        scale[g]=am>0.f ? am/127.f : (group==n ? 1.f : 0.f);
        float inv=am>0.f ? (group==n ? 1.f/scale[g] : 127.f/am) : 0.f;
        int32_t s=0;
        for (int i=0; i<group; i++) { int v=(int)lrintf(x[g*group+i]*inv); if(v>127)v=127; if(v< -127)v=-127; q[g*group+i]=(int8_t)v; s+=v; }
        sums[g]=s;
    }
}
static TARGET("ssse3") __m128i dot_four_groups(__m128i u, __m128i x) {
    return _mm_madd_epi16(_mm_maddubs_epi16(u,x),_mm_set1_epi16(1));
}
static TARGET("ssse3") void dot_unsigned_ssse3(const uint8_t *u, const int8_t *x, int32_t *y) {
    for(int h=0;h<2;h++) {
        __m128i a=dot_four_groups(_mm_loadu_si128((const __m128i *)(u+16*h)),_mm_loadu_si128((const __m128i *)(x+16*h)));
        __m128i b=dot_four_groups(_mm_loadu_si128((const __m128i *)(u+32+16*h)),_mm_loadu_si128((const __m128i *)(x+32+16*h)));
        _mm_storeu_si128((__m128i *)(y+4*h),_mm_add_epi32(a,b));
    }
}
static TARGET("avx2") void dot_unsigned_avx2(const uint8_t *u, const int8_t *x, int32_t *y) {
    __m256i one=_mm256_set1_epi16(1), a=_mm256_loadu_si256((const __m256i *)u), b=_mm256_loadu_si256((const __m256i *)(u+32));
    a=_mm256_madd_epi16(_mm256_maddubs_epi16(a,_mm256_loadu_si256((const __m256i *)x)),one);
    b=_mm256_madd_epi16(_mm256_maddubs_epi16(b,_mm256_loadu_si256((const __m256i *)(x+32))),one);
    _mm256_storeu_si256((__m256i *)y,_mm256_add_epi32(a,b));
}
static TARGET("ssse3") int32_t dot_signed_abs(const int8_t *w,const int8_t *x,int n) {
    __m128i sum=_mm_setzero_si128(); int i=0;
    for(;i+16<=n;i+=16) {
        __m128i a=_mm_loadu_si128((const __m128i *)(w+i)),b=_mm_loadu_si128((const __m128i *)(x+i));
        sum=_mm_add_epi32(sum,dot_four_groups(_mm_abs_epi8(a),_mm_sign_epi8(b,a)));
    }
    int32_t t[4];_mm_storeu_si128((__m128i *)t,sum);int32_t out=t[0]+t[1]+t[2]+t[3];
    for(;i<n;i++)out+=(int32_t)w[i]*x[i]; return out;
}
static TARGET("sse2") int32_t dot_signed16(const int16_t *w,const int16_t *x,int n) {
    __m128i sum=_mm_setzero_si128();int i=0;
    for(;i+8<=n;i+=8)sum=_mm_add_epi32(sum,_mm_madd_epi16(_mm_loadu_si128((const __m128i *)(w+i)),_mm_loadu_si128((const __m128i *)(x+i))));
    int32_t t[4];_mm_storeu_si128((__m128i *)t,sum);int32_t out=t[0]+t[1]+t[2]+t[3];
    for(;i<n;i++)out+=(int32_t)w[i]*x[i];return out;
}
static TARGET("ssse3") int32_t dot_lookup(const uint8_t *u,const int8_t *x,int n) {
    int32_t total=0;
    for(int i=0;i<n;i+=4) {
        uint8_t low[16],high[16],indices[16]={0};
        for(int mask=0;mask<16;mask++) {
            int16_t v=0;for(int j=0;j<4;j++)if(mask&(1<<j))v+=x[i+j];
            low[mask]=(uint8_t)v;high[mask]=(uint8_t)((uint16_t)v>>8);
        }
        for(int bit=0;bit<4;bit++)for(int j=0;j<4;j++)indices[bit]|=(uint8_t)(((u[i+j]>>bit)&1)<<j);
        __m128i ix=_mm_loadu_si128((const __m128i *)indices);
        __m128i lo=_mm_shuffle_epi8(_mm_loadu_si128((const __m128i *)low),ix);
        __m128i hi=_mm_shuffle_epi8(_mm_loadu_si128((const __m128i *)high),ix);
        int16_t v[8];_mm_storeu_si128((__m128i *)v,_mm_unpacklo_epi8(lo,hi));
        total+=v[0]+2*v[1]+4*v[2]+8*v[3]-8*(x[i]+x[i+1]+x[i+2]+x[i+3]);
    }
    return total;
}
static void fma_lanes_scalar(const float *w,const float *x,float *y,int n) {
    for(int l=0;l<8;l++)y[l]=0.f;
    for(int i=0;i<n;i++)y[i&7]=fmaf(w[i],x[i],y[i&7]);
}
static TARGET("avx,fma") void fma_lanes_avx(const float *w,const float *x,float *y,int n) {
    __m256 a=_mm256_setzero_ps();for(int i=0;i<n;i+=8)a=_mm256_fmadd_ps(_mm256_loadu_ps(w+i),_mm256_loadu_ps(x+i),a);
    _mm256_storeu_ps(y,a);
}
static float reduce_float(const float *x,int n,int order) {
    if(order==0){float s=0.f;for(int i=0;i<n;i++)s+=x[i];return s;}
    if(order==1){float a[8]={0};for(int i=0;i<n;i++)a[i&7]+=x[i];float b0=a[0]+a[4],b1=a[1]+a[5],b2=a[2]+a[6],b3=a[3]+a[7];return(b0+b2)+(b1+b3);}
    float a[64];memcpy(a,x,(size_t)n*4);for(int step=n/2;step;step/=2)for(int i=0;i<step;i++)a[i]+=a[i+step];return a[0];
}
static void print_f32(const float *p,int n){putchar('[');for(int i=0;i<n;i++){if(i)putchar(',');if(p[i]==0.f&&signbit(p[i]))printf("-0.0");else printf("%.9g",p[i]);}putchar(']');}
static void print_integer(const void *p,int kind,int n){putchar('[');for(int i=0;i<n;i++){if(i)putchar(',');printf("%d",integer_at(p,kind,i));}putchar(']');}
