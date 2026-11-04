/*
 * fx_text.c - pixel text stamp for the Kodak Charmera (effect 15, after Faded).
 *
 * Flow (photo mode, effect wheel on "Text"):
 *   Play/OK opens the editor. Up/Down change the value, Play/OK confirms and moves on:
 *     letters (pick the tick to finish) -> colour -> X -> Y -> rotation (15 deg) -> size (10 %)
 *   The setup lives in RAM (this code cave is loaded at boot), so it is kept until power-off.
 *   The viewfinder shows the text live; the shot gets it burned in after capture.
 *
 * Built freestanding for ARM7TDMI (ARM mode) and linked into dead firmware regions by
 * tools/build_fx.py. Firmware entry points are absolute symbols from the linker script.
 */
typedef unsigned char u8;
typedef signed char s8;
typedef unsigned short u16;
typedef short s16;
typedef unsigned int u32;
typedef int s32;
typedef long long s64;

#include "fx_tables.h"

#define EFFECT (*(volatile u8 *)0x10FBC5)   /* effect index used by the stock firmware */
#define CAPTURE ((volatile u32 *)0x14AB6C)  /* [1]=width [2]=height [5]=GP420 buffer */
#define EFFECT_TEXT 15
#define MAXLEN 12
#define LCD_W 160
#define PV_X0 24                            /* photo area of the 160x80 viewfinder */
#define PV_W 112
#define PV_H 80
#define GRID 20                             /* X/Y positions 0..20 = 0..100 % in 5 % steps */

enum { ST_TEXT, ST_COLOR, ST_X, ST_Y, ST_ROT, ST_SIZE };

extern void FW_OSD_HIDE(int a, int b, int c);

struct fxstate {
    u8 editing, step, cur, cand;
    u8 len, color, x, y;
    u8 rot, scale, pad0, pad1;
    u8 text[MAXLEN];
};
/* .data: initial values come from the flash image, survive until power-off */
struct fxstate fx = {0, ST_TEXT, 0, 0, 0, 0, GRID / 2, GRID / 2, 0, 20, 0, 0, {0}};   /* size 200 % */

/* ------------------------------------------------------------------ rendering */

struct dst {
    u8 *buf;
    u32 stride;     /* GP420: bytes per line (= width) */
    int fmt;        /* 0 = RGB565 160-wide viewfinder, 1 = GP420 photo */
    int cx0, cy0, cx1, cy1;                 /* clip rectangle */
    const struct pal *pal;
    int hl;         /* highlighted character index or -1 */
};

static u32 udiv(u32 n, u32 d)
{
    u32 q = 0, r = 0;
    int i;
    if (!d) return 0;
    for (i = 31; i >= 0; i--) {
        r = (r << 1) | ((n >> i) & 1);
        if (r >= d) { r -= d; q |= 1u << i; }
    }
    return q;
}

/* kind: 1 outline, 2 fill, 3 highlight background, 4 highlighted glyph */
static void put(const struct dst *d, int x, int y, int kind)
{
    const struct pal *p = d->pal;
    if (d->fmt == 0) {
        u16 c = (kind == 1 || kind == 4) ? p->o565 : p->c565;
        ((u16 *)d->buf)[y * LCD_W + x] = c;
    } else {
        u32 line = (u32)(y >> 1) * 3;
        u8 *c;
        int o = (kind == 1 || kind == 4);
        d->buf[(line + ((y & 1) << 1)) * d->stride + (u32)x] = o ? p->oy : p->y;
        c = d->buf + (line + 1) * d->stride + ((u32)x & ~1u);
        c[0] = o ? p->ou : p->u;
        c[1] = o ? p->ov : p->v;
    }
}

static int gbit(const u8 *t, int n, int tx, int ty)
{
    u32 k, col;
    if ((u32)ty > 6 || tx < 0) return 0;
    k = (u32)tx / 6;
    if (k >= (u32)n) return 0;
    col = (u32)tx - k * 6;
    if (col == 5) return 0;
    return (FONT[t[k]][col] >> ty) & 1;
}

/* padded text space: X in [0, n*6+1), Y in [0, 9); glyph pixel (X-1, Y-1) */
static int sample(const struct dst *d, const u8 *t, int n, int X, int Y)
{
    int tx = X - 1, ty = Y - 1, dx, dy;
    int g = gbit(t, n, tx, ty);
    if (d->hl >= 0 && X >= d->hl * 6 && X < d->hl * 6 + 7)
        return g ? 4 : 3;
    if (g) return 2;
    for (dy = -1; dy <= 1; dy++)
        for (dx = -1; dx <= 1; dx++)
            if (gbit(t, n, tx + dx, ty + dy)) return 1;
    return 0;
}

/*
 * Draw text centred on (cx, cy) in destination pixels.
 * kx/ky: font pixels per destination pixel (Q16) along x/y; rot: 0..23 (15 deg steps, CCW).
 * rx/ry: half size of the box to scan, in destination pixels.
 */
static void render(const struct dst *d, const u8 *t, int n, int cx, int cy,
                   s32 kx, s32 ky, int rot, int rx, int ry)
{
    s32 c = COS24[rot], s = COS24[(rot + 18) % 24];
    s32 a11 = (s32)(((s64)c * kx) >> 14), a12 = -(s32)(((s64)s * ky) >> 14);
    s32 a21 = (s32)(((s64)s * kx) >> 14), a22 = (s32)(((s64)c * ky) >> 14);
    int tw = n * 6 + 1, th = 9;
    s32 w2 = (s32)tw << 15, h2 = (s32)th << 15;
    int x0 = cx - rx, x1 = cx + rx + 1, y0 = cy - ry, y1 = cy + ry + 1, u, v;
    if (x0 < d->cx0) x0 = d->cx0;
    if (y0 < d->cy0) y0 = d->cy0;
    if (x1 > d->cx1) x1 = d->cx1;
    if (y1 > d->cy1) y1 = d->cy1;
    for (v = y0; v < y1; v++) {
        s32 dv = v - cy, du = x0 - cx;
        s32 fx = du * a11 + dv * a12 + w2;
        s32 fy = du * a21 + dv * a22 + h2;
        for (u = x0; u < x1; u++, fx += a11, fy += a21) {
            int X, Y, k;
            if (fx < 0 || fy < 0) continue;
            X = fx >> 16;
            Y = fy >> 16;
            if (X >= tw || Y >= th) continue;
            k = sample(d, t, n, X, Y);
            if (k) put(d, u, v, k);
        }
    }
}

/* place the user's text using its position/rotation/size, mapped into a dw x dh area at (ox, oy).
 * Design space is 800x600; one font pixel = 0.6 design px per 10 % of size (6 px at 100 %). */
static void place(const struct dst *d, int ox, int oy, int dw, int dh)
{
    u32 pq8 = (u32)fx.scale * 1536u / 10u;          /* design px per font px, Q8 */
    u32 denx = (pq8 * (u32)dw) >> 4, deny = (pq8 * (u32)dh) >> 4;
    int n = fx.len, r;
    if (!n || !denx || !deny) return;
    r = (n * 6 + 1 + 9) / 2 + 1;
    render(d, fx.text, n,
           ox + (int)((u32)fx.x * (u32)dw / GRID), oy + (int)((u32)fx.y * (u32)dh / GRID),
           (s32)udiv(800u << 20, denx), (s32)udiv(600u << 20, deny), fx.rot,
           (int)((u32)r * denx / 12800u) + 2, (int)((u32)r * deny / 9600u) + 2);
}

/* unrotated text, f screen pixels per font pixel, top-left at (x, y) */
static void label(struct dst *d, const u8 *t, int n, int x, int y, int f)
{
    int tw = (n * 6 + 1) * f;
    render(d, t, n, x + tw / 2, y + 9 * f / 2, (1 << 16) / f, (1 << 16) / f, 0, tw / 2 + 2, 5 * f);
}

/* 2x if it fits the 112-px photo area, else 1x; centred horizontally when cx is set */
static void label_fit(struct dst *d, const u8 *t, int n, int y, int centre)
{
    int f = (n * 6 + 1) * 2 <= PV_W - 4 ? 2 : 1;
    int x = centre ? PV_X0 + (PV_W - (n * 6 + 1) * f) / 2 : PV_X0 + 2;
    label(d, t, n, x, y, f);
}

static int glyph(int ch)
{
    if (ch >= 'A' && ch <= 'Z') return ch - 'A';
    if (ch >= '0' && ch <= '9') return GLYPH_0 + ch - '0';
    if (ch == ':') return GLYPH_COLON;
    return GLYPH_SPACE;
}

static int str_glyphs(u8 *out, const char *s, int value)
{
    int n = 0, i, digits[3], nd = 0;
    while (*s) out[n++] = (u8)glyph(*s++);
    if (value >= 0) {
        do { digits[nd++] = value % 10; value /= 10; } while (value && nd < 3);
        for (i = nd - 1; i >= 0; i--) out[n++] = (u8)(GLYPH_0 + digits[i]);
    }
    return n;
}

static const struct pal LABEL_PAL = {0xFFFF, 0x0000, 255, 128, 128, 0, 128, 128};

/* ------------------------------------------------------------------ hooks */

/* key: 0 = Play/OK, 1 = Up, 2 = Down. Returns 1 when the key was used by the editor. */
int fx_key(int key)
{
    struct fxstate *s = &fx;
    int d = key == 1 ? 1 : (key == 2 ? -1 : 0), v;
    if (EFFECT != EFFECT_TEXT) { s->editing = 0; return 0; }
    if (!s->editing) {
        if (key != 0) return 0;
        s->editing = 1;
        s->step = ST_TEXT;
        s->cur = 0;
        s->cand = s->len ? s->text[0] : 0;
        return 1;
    }
    switch (s->step) {
    case ST_TEXT:
        if (d) {
            v = s->cand + d;
            if (v < 0) v = NGLYPH - 1;
            if (v >= NGLYPH) v = 0;
            s->cand = (u8)v;
        } else if (s->cand == GLYPH_DONE) {
            s->len = s->cur;
            s->step = ST_COLOR;
        } else {
            s->text[s->cur++] = s->cand;
            if (s->cur > s->len) s->len = s->cur;
            if (s->cur >= MAXLEN) s->step = ST_COLOR;
            else s->cand = s->cur < s->len ? s->text[s->cur] : GLYPH_DONE;
        }
        break;
    case ST_COLOR:
        if (d) s->color = (u8)((s->color + NCOLOR + d) % NCOLOR);
        else s->step = ST_X;
        break;
    case ST_X:
    case ST_Y:
        if (d) {
            u8 *p = s->step == ST_X ? &s->x : &s->y;
            v = *p + (s->step == ST_X ? d : -d);       /* Up = right / up */
            if (v < 0) v = 0;
            if (v > GRID) v = GRID;
            *p = (u8)v;
        } else s->step++;
        break;
    case ST_ROT:
        if (d) s->rot = (u8)((s->rot + 24 + d) % 24);
        else s->step = ST_SIZE;
        break;
    default: /* ST_SIZE */
        if (d) {
            v = s->scale + d;
            if (v < 1) v = 1;
            if (v > 40) v = 40;
            s->scale = (u8)v;
        } else {
            s->editing = 0;
            s->step = ST_TEXT;
        }
        break;
    }
    return 1;
}

/* called by the display task with the 160x80 RGB565 viewfinder frame */
void fx_preview(u16 *frame)
{
    struct dst d;
    u8 tmp[MAXLEN + 1], lab[10];
    int n;
    if (EFFECT != EFFECT_TEXT || !frame) return;
    if (fx.color >= NCOLOR) fx.color = 0;
    d.buf = (u8 *)frame;
    d.stride = LCD_W;
    d.fmt = 0;
    d.cx0 = PV_X0;
    d.cy0 = 0;
    d.cx1 = PV_X0 + PV_W;
    d.cy1 = PV_H;
    d.pal = &PAL[fx.color];
    d.hl = -1;
    if (fx.editing && fx.step == ST_TEXT) {
        for (n = 0; n < fx.cur && n < MAXLEN; n++) tmp[n] = fx.text[n];
        tmp[n++] = fx.cand;
        d.hl = n - 1;
        label_fit(&d, tmp, n, 40, 1);
        d.hl = -1;
        d.pal = &LABEL_PAL;
        label_fit(&d, lab, str_glyphs(lab, "TEXT", -1), 1, 0);
        return;
    }
    place(&d, PV_X0, 0, PV_W, PV_H);
    d.pal = &LABEL_PAL;
    if (!fx.editing) {
        if (!fx.len) label_fit(&d, lab, str_glyphs(lab, "OK:EDIT", -1), 32, 1);
        return;
    }
    switch (fx.step) {
    case ST_COLOR: n = str_glyphs(lab, "COLOR", -1); break;
    case ST_X:     n = str_glyphs(lab, "X ", fx.x * 5); break;
    case ST_Y:     n = str_glyphs(lab, "Y ", fx.y * 5); break;
    case ST_ROT:   n = str_glyphs(lab, "ROT ", fx.rot * 15); break;
    default:       n = str_glyphs(lab, "SIZE ", fx.scale * 10); break;
    }
    label_fit(&d, lab, n, 1, 0);
}

/* called after the photo is captured (after frames), before JPEG encoding */
void fx_capture(void)
{
    struct dst d;
    u32 w = CAPTURE[1], h = CAPTURE[2];
    u8 *buf = (u8 *)CAPTURE[5];
    if (EFFECT != EFFECT_TEXT || !fx.len || !buf) return;
    if (w < 16 || h < 16 || w > 4096 || h > 4096 || (w & 1)) return;
    if (fx.color >= NCOLOR) fx.color = 0;
    d.buf = buf;
    d.stride = w;
    d.fmt = 1;
    d.cx0 = 0;
    d.cy0 = 0;
    d.cx1 = (int)w;
    d.cy1 = (int)(h & ~1u);
    d.pal = &PAL[fx.color];
    d.hl = -1;
    place(&d, 0, 0, (int)w, (int)h);
}

/* effect -> OSD icon id (stock icons 0x60..0x6A, new slots 0x2A..0x2D) */
int fx_icon_for_effect(int e)
{
    static const u8 map[16] = {0, 0x60, 0x61, 0x62, 0x63, 0x64, 0x65, 0x66,
                               0x67, 0x68, 0x69, 0x6A, 0x2A, 0x2B, 0x2C, 0x2D};
    return (u32)e < 16 ? map[e] : 0;
}

void fx_hide_effect_icons(void)
{
    FW_OSD_HIDE(0x69, 0x6A, 0x2A);
    FW_OSD_HIDE(0x2B, 0x2C, 0x2D);
}

/* ------------------------------------------------------------------ firmware entry stubs */

#define STUB_KEY(name, code, cont)                                      \
    __attribute__((naked, used)) void name(void)                        \
    {                                                                   \
        __asm__ volatile("push {r0-r3, r12, lr}\n"                      \
                         "mov r0, #" #code "\n"                         \
                         "bl fx_key\n"                                  \
                         "cmp r0, #0\n"                                 \
                         "pop {r0-r3, r12, lr}\n"                       \
                         "bne FW_KEY_DONE\n"                            \
                         "b " #cont "\n");                              \
    }

/* Up (msg 0x7002) and Down (msg 0x7003): editor first, else stock effect handlers */
STUB_KEY(stub_up, 1, FW_UP_HANDLER)
STUB_KEY(stub_down, 2, FW_DOWN_HANDLER)

/* Play/OK (msg 0x7000): replaces "bl FW_OK_FIRST_CALL" at the start of the stock handler */
__attribute__((naked, used)) void stub_ok(void)
{
    __asm__ volatile("push {r0-r3, r12, lr}\n"
                     "mov r0, #0\n"
                     "bl fx_key\n"
                     "cmp r0, #0\n"
                     "pop {r0-r3, r12, lr}\n"
                     "bne FW_KEY_DONE\n"
                     "bl FW_OK_FIRST_CALL\n"
                     "b FW_OK_RESUME\n");
}

/* display task, effects other than Pixel: draw on the frame (r5) then continue */
__attribute__((naked, used)) void stub_disp(void)
{
    __asm__ volatile("push {r0-r3, r12, lr}\n"
                     "mov r0, r5\n"
                     "bl fx_preview\n"
                     "pop {r0-r3, r12, lr}\n"
                     "b FW_DISP_RESUME\n");
}

/* post-capture: run stock frame compositing, then stamp the text */
__attribute__((naked, used)) void stub_cap(void)
{
    __asm__ volatile("push {r4, lr}\n"
                     "bl FW_FRAME_APPLY\n"
                     "bl fx_capture\n"
                     "mov r0, #0\n"
                     "pop {r4, pc}\n");
}
