/* Memory regions are dead, unreferenced firmware code (see tools/free_spans.py).
   RAM address = flash offset - 0x200. Regions are loaded to RAM at boot. */
MEMORY {
  CODE  (rx) : ORIGIN = 0x036A60, LENGTH = 0x372A8 - 0x36A60   /* dead USB-UVC request handler */
  CODE2 (rx) : ORIGIN = 0x0893A4, LENGTH = 0x89A00 - 0x893A4
  CODE3 (rx) : ORIGIN = 0x0DF790, LENGTH = 0xDFC54 - 0xDF790
  DATA  (rw) : ORIGIN = 0x0753A4, LENGTH = 0x75910 - 0x753A4
}
/* stock firmware entry points (RAM addresses) */
FW_OSD_HIDE       = 0x021C14;
FW_KEY_DONE       = 0x01E0D4;   /* photo-mode loop: message handled */
FW_UP_HANDLER     = 0x01DC24;   /* stock handler for Up (msg 0x7003): previous effect */
FW_DOWN_HANDLER   = 0x01DCB4;   /* stock handler for Down (msg 0x7002): next effect */
FW_PLAY_STOCK     = 0x01DEC0;   /* stock Play/OK (msg 0x7061): post state r1 = 0x104, playback */
FW_DISP_RESUME    = 0x016120;   /* display task after the Pixel-filter branch */
FW_FRAME_APPLY    = 0x00ECF0;   /* stock frame compositing after capture */
SECTIONS {
  .text2  : { *(.text.fx_preview) *(.text.fx_key) *(.text.stub_*) *(.text.fx_icon_for_effect)
              *(.text.fx_hide_effect_icons) } > CODE2
  .text3  : { *(.text.fx_capture) *(.text.str_glyphs) *(.text.label) *(.text.label_fit) *(.text.glyph) } > CODE3
  .text   : { *(.text*) } > CODE
  .rodata : { *(.rodata*) } > DATA
  .data   : { *(.data*) *(.bss*) *(COMMON) } > DATA
  /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment) *(.note*) }
}
