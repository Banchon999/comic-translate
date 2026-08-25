/*
 * comic_core.h — C ABI sketch for the in-process alternative (Option 2).
 *
 * This is the counter-proposal to `docs/architecture/proto/`, written out so
 * the two can be compared on their real shapes rather than on adjectives. It
 * is NOT the recommended first step; see
 * `docs/architecture/text-canvas-migration.md`.
 *
 * The rules a Dart FFI / C++ boundary imposes, none of which gRPC imposes:
 *
 *   1. No C++ in the ABI. No std::string, no std::vector, no exceptions
 *      crossing the line, no classes with vtables. Dart FFI binds C.
 *   2. Every allocation has one owner and one named free function. A Dart
 *      finalizer that calls the wrong one is a heap corruption, not an error.
 *   3. Strings are UTF-8, NUL-terminated, and their lifetime is documented per
 *      field. Text offsets are UTF-16 code units (Dart indexes UTF-16).
 *   4. Errors are return codes plus a thread-local last-error string. There is
 *      no other channel.
 *   5. Long operations take a callback and a cancel token, because the FFI
 *      call blocks the isolate that made it.
 *
 * The reason this file is short and the proto files are long is not that the
 * FFI design is simpler. It is that the proto files describe a boundary the
 * Python pipeline can actually sit behind today, and this one describes a
 * boundary that presupposes ~25k lines of `modules/` already exist in C++.
 */

#ifndef COMIC_CORE_H
#define COMIC_CORE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* -------------------------------------------------------------------------
 * Lifetime and errors
 * ------------------------------------------------------------------------- */

typedef struct CtSession CtSession; /* opaque */

typedef enum {
  CT_OK = 0,
  CT_ERR_INVALID_ARGUMENT = 1,
  CT_ERR_NOT_FOUND = 2,
  CT_ERR_MODEL_UNAVAILABLE = 3,
  CT_ERR_CANCELLED = 4,
  CT_ERR_INTERNAL = 5
} CtStatus;

/* Thread-local. Valid until the next call on the same thread. */
const char* ct_last_error(void);

CtSession* ct_session_open(const char* app_data_dir);
void       ct_session_close(CtSession*);

/* -------------------------------------------------------------------------
 * Buffers
 *
 * Borrowed vs owned is the whole game here. A page raster passed IN is
 * borrowed: the caller guarantees it outlives the call and the core must not
 * retain it. A raster passed OUT is owned by the core and released by the
 * matching ct_*_free. Mixing the two is the defect this comment exists to
 * prevent.
 * ------------------------------------------------------------------------- */

typedef enum {
  CT_PIXEL_RGB888 = 1,
  CT_PIXEL_RGBA8888 = 2,
  CT_PIXEL_GRAY8 = 3
} CtPixelFormat;

typedef struct {
  uint8_t* data;
  uint32_t width;
  uint32_t height;
  uint32_t stride;
  CtPixelFormat format;
} CtImage;

void ct_image_free(CtImage*);

typedef struct { int32_t x1, y1, x2, y2; } CtBox;
typedef struct { float x, y; } CtPointF;

/* -------------------------------------------------------------------------
 * TextBlock
 *
 * Flat, fixed-size, no nested owning pointers except the two arrays whose
 * lengths sit beside them. Anything richer costs a marshalling pass per field
 * on the Dart side, which is exactly what FFI was chosen to avoid.
 * ------------------------------------------------------------------------- */

typedef enum { CT_CLASS_BUBBLE = 1, CT_CLASS_FREE = 2 } CtTextClass;
typedef enum { CT_DIR_HORIZONTAL = 1, CT_DIR_VERTICAL = 2 } CtTextDirection;

typedef struct {
  uint64_t        id;
  CtBox           text_bbox;
  CtBox           bubble_bbox;    /* zeroed when absent */
  const CtPointF* segm_points;    /* owned by the block */
  uint32_t        segm_point_count;
  CtTextClass     text_class;
  float           angle;
  const char*     text;           /* UTF-8, owned by the block */
  const char*     translation;
  float           line_spacing;
  int32_t         min_font_size;
  int32_t         max_font_size;
  uint32_t        font_color_rgba;
  CtTextDirection direction;
} CtTextBlock;

typedef struct {
  CtTextBlock* items;
  uint32_t     count;
} CtTextBlockList;

void ct_textblock_list_free(CtTextBlockList*);

/* -------------------------------------------------------------------------
 * Stages
 *
 * `user_data` is passed back to every callback untouched. The callback runs on
 * the core's worker thread, so a Dart binding must marshal it to the isolate
 * through a NativeCallable::listener — calling into Dart directly from a
 * foreign thread is undefined.
 *
 * Returning 0 from the progress callback cancels the operation, which is the
 * only cancellation channel. A separate ct_cancel() would need a handle the
 * blocking call has not returned yet.
 * ------------------------------------------------------------------------- */

typedef int (*CtProgressFn)(void* user_data,
                            const char* stage,
                            int32_t step,
                            int32_t step_count);

CtStatus ct_detect(CtSession*,
                   const CtImage* page,      /* borrowed */
                   CtProgressFn, void* user_data,
                   CtTextBlockList* out);    /* caller frees via ct_textblock_list_free */

CtStatus ct_ocr(CtSession*,
                const CtImage* page,
                CtTextBlockList* blocks,     /* mutated in place: .text filled */
                CtProgressFn, void* user_data);

CtStatus ct_translate(CtSession*,
                      const CtImage* page,
                      CtTextBlockList* blocks, /* .translation filled */
                      CtProgressFn, void* user_data);

/* Cleaning returns patches rather than a whole page, matching how the Qt
 * canvas composites them (`app/ui/commands/base.py`'s PatchCommandBase) and
 * how the PSD exporter writes them as their own layer group. */
typedef struct {
  CtBox   bbox;
  CtImage image;
  char    hash[65]; /* hex sha256 + NUL */
} CtPatch;

typedef struct { CtPatch* items; uint32_t count; } CtPatchList;
void ct_patch_list_free(CtPatchList*);

CtStatus ct_clean(CtSession*,
                  const CtImage* page,
                  const CtImage* mask,       /* CT_PIXEL_GRAY8, borrowed */
                  CtProgressFn, void* user_data,
                  CtPatchList* out);

/* -------------------------------------------------------------------------
 * Text layout
 *
 * Present for the same reason TextLayoutService is present in the proto: if
 * the core auto-fits with one shaper and the UI paints with another, the
 * preview and the export diverge. In-process this is at least cheap enough to
 * call per reflow, which is the one place the FFI boundary genuinely beats the
 * gRPC one.
 * ------------------------------------------------------------------------- */

typedef struct {
  uint32_t start, end;  /* UTF-16 code units */
  CtPointF origin;
  float    width, ascent, descent;
} CtLineBox;

typedef struct {
  const char* wrapped_text; /* UTF-8, owned by the result */
  float       font_size;
  CtLineBox*  lines;
  uint32_t    line_count;
} CtFitResult;

void ct_fit_result_free(CtFitResult*);

CtStatus ct_autofit(CtSession*,
                    const char* text,        /* UTF-8 */
                    CtBox roi,
                    const char* font_family,
                    float line_spacing,
                    int32_t min_font_size,
                    int32_t max_font_size,
                    int vertical,
                    int no_space_language,
                    CtFitResult* out);

#ifdef __cplusplus
}
#endif
#endif /* COMIC_CORE_H */
