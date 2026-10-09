"""The translator reports who speaks each block, and a webtoon strip is
translated in one request.

Into Thai the LLM returns {"speaker", "gender", "translation"} per block; a
line whose ครับ/ค่ะ contradicts the gender the model itself gave is reported
for a person to fix (never changed). In webtoon batch every chunk of a strip
is OCR'd first and the strip goes out as one translation, so the model sees
the whole conversation.
"""

import types

import numpy as np
import pytest

from modules.utils import thai_speech as ts
from modules.utils.glossary import issue_rows
from modules.utils.prompts import PromptManager
from modules.utils.strip_sheet import column_count, sheet_note, strip_sheet
from modules.utils.textblock import TextBlock
from modules.utils.translator_utils import previous_lines, set_texts_from_json, speakers_so_far


def _blk(text="x", translation="", speaker="", gender=""):
    blk = TextBlock(text=text, translation=translation)
    blk.speaker, blk.speaker_gender = speaker, gender
    return blk


class TestReply:
    def test_object_values_fill_translation_speaker_and_gender(self):
        blocks = [_blk(), _blk()]
        set_texts_from_json(blocks, 'Sure: {"block_0": {"speaker": " ลีน่า ", "gender": "Female", '
                                    '"translation": "ไปกันค่ะ"}, "block_1": {"speaker": "narration", '
                                    '"gender": "unknown", "translation": "ณ ปราสาท"}}')
        assert [(b.translation, b.speaker, b.speaker_gender) for b in blocks] == [
            ("ไปกันค่ะ", "ลีน่า", "female"),
            ("ณ ปราสาท", "narration", ""),
        ]

    def test_a_plain_reply_still_works_and_clears_an_old_speaker(self):
        blocks = [_blk(speaker="ไค", gender="male")]
        set_texts_from_json(blocks, '{"block_0": "ฮ่า"}')
        assert (blocks[0].translation, blocks[0].speaker, blocks[0].speaker_gender) == ("ฮ่า", "", "")

    @pytest.mark.parametrize("raw, expected", [
        ("male", "male"), ("M", "male"), ("ชาย", "male"), ("female", "female"),
        ("หญิง", "female"), ("neutral", ""), ("unknown", ""), (None, ""),
    ])
    def test_genders_are_normalised(self, raw, expected):
        blocks = [_blk()]
        set_texts_from_json(blocks, '{"block_0": {"translation": "a", "gender": %s}}'
                            % ("null" if raw is None else f'"{raw}"'))
        assert blocks[0].speaker_gender == expected

    def test_a_deep_copy_keeps_the_speaker(self):
        copy = _blk(speaker="ลีน่า", gender="female").deep_copy()
        assert (copy.speaker, copy.speaker_gender) == ("ลีน่า", "female")

    def test_an_old_project_block_gets_empty_fields(self):
        from app.projects.parsers import ProjectDecoder

        blk = ProjectDecoder.decode_textblock({"data": {"text": "x", "translation": "y"}})
        assert (blk.speaker, blk.speaker_gender) == ("", "")


class TestPrompt:
    def test_thai_asks_for_the_speaker_shape(self, tmp_path):
        prompt = PromptManager(str(tmp_path / "p.json")).build_system_prompt("Korean", "Thai")
        assert prompt.count(ts.SPEAKER_OUTPUT_HEADER) == 1
        assert '"speaker"' in prompt and '"gender"' in prompt
        # After the general contract, so it is the shape the model reads last.
        assert prompt.index(ts.SPEAKER_OUTPUT_HEADER) > prompt.index("DO NOT translate the keys")

    def test_other_targets_keep_the_plain_shape(self, tmp_path):
        prompt = PromptManager(str(tmp_path / "p.json")).build_system_prompt("Korean", "English")
        assert ts.SPEAKER_OUTPUT_HEADER not in prompt


class TestSpeakerCheck:
    def test_a_female_speaker_saying_krub_is_flagged_with_her_name(self):
        (issue,) = ts.check_speech([_blk(translation="ไปกันเถอะครับ", speaker="ลีน่า", gender="female")])
        assert (issue.reason, issue.speaker) == (ts.FEMALE_SPEAKER_MALE_SPEECH, "ลีน่า")

    def test_a_male_speaker_saying_ka_or_dichan_is_flagged(self):
        for line in ("ได้ค่ะ", "ดิฉันไม่รู้"):
            (issue,) = ts.check_speech([_blk(translation=line, speaker="ไค", gender="male")])
            assert issue.reason == ts.MALE_SPEAKER_FEMALE_SPEECH

    @pytest.mark.parametrize("line, gender", [
        ("ไปกันค่ะ", "female"),
        ("ไปกันครับ", "male"),
        ("ผมเปียกหมดเลย", "female"),   # hair, not "I"
        ("คะแนนดีมาก", "male"),        # คะแนน is not the particle คะ
        ("พ่ะย่ะค่ะ", "male"),          # a man to royalty
        ("ไปกันครับ", ""),              # gender unknown: never judged
        ("ไปกันค่ะ", "unknown"),
    ])
    def test_agreeing_or_unknown_lines_are_not(self, line, gender):
        assert ts.check_speech([_blk(translation=line, speaker="x", gender=gender)]) == []

    def test_a_line_contradicting_itself_is_reported_as_that_only(self):
        (issue,) = ts.check_speech([_blk(translation="ครับ ขอบคุณค่ะ", speaker="ไค", gender="male")])
        assert (issue.reason, issue.speaker) == (ts.MIXED, "")

    def test_the_report_row_carries_the_speaker(self):
        rows = issue_rows([ts.SpeechIssue(0, "ไปครับ", ts.FEMALE_SPEAKER_MALE_SPEECH, "ลีน่า")])
        assert rows == [("ไปครับ", ts.FEMALE_SPEAKER_MALE_SPEECH, "speech", "ลีน่า")]

    def test_the_warning_names_the_speaker(self, qapp):
        from app.ui.messages import Messages

        issue = ts.SpeechIssue(0, "ไปครับ", ts.FEMALE_SPEAKER_MALE_SPEECH, "ลีน่า")
        text = Messages.glossary_issues_text([issue])
        assert "«ไปครับ»" in text and "ลีน่า" in text and "female" in text
        assert "the speaker" in Messages.speech_reason_text(ts.MALE_SPEAKER_FEMALE_SPEECH)

    def test_the_translator_reports_it_after_an_llm_reply(self, tmp_path):
        """End to end through BaseLLMTranslation: the reply's shape decides it."""
        from modules.translation.llm.base import BaseLLMTranslation
        from modules.translation.processor import Translator

        class Fake(BaseLLMTranslation):
            def _perform_translation(self, user_prompt, system_prompt, image):
                return ('{"block_0": {"speaker": "ลีน่า", "gender": "female", "translation": "ได้ครับ"},'
                        ' "block_1": {"speaker": "ไค", "gender": "male", "translation": "ไปครับ"}}')

        engine = Fake()
        engine.source_lang, engine.target_lang = "Korean", "Thai"
        engine.get_system_prompt = lambda s, t: ""
        blocks = [_blk("가요"), _blk("가")]
        engine.translate(blocks, np.zeros((4, 4, 3), np.uint8), "")

        translator = Translator.__new__(Translator)
        translator.is_llm_engine = False
        translator.target_lang_en = "Thai"
        (issue,) = translator.check_glossary(blocks)
        assert (issue.block_index, issue.speaker) == (0, "ลีน่า")


class TestSpeakersSoFar:
    def test_lists_each_named_speaker_once_without_narration(self):
        line = speakers_so_far([
            _blk(speaker="ลีน่า", gender="female"), _blk(speaker="narration"),
            _blk(speaker="ไค"), _blk(speaker="ไค", gender="male"), _blk(speaker="sfx"),
        ])
        assert "ลีน่า (female), ไค (male)" in line and "narration" not in line

    def test_nothing_named_gives_nothing(self):
        assert speakers_so_far([_blk(), _blk(speaker="narration")]) == ""

    def test_previous_lines_carry_who_said_what(self):
        blocks = [_blk(f"line{i}", speaker="하린" if i % 2 else "") for i in range(9)]
        text = previous_lines(blocks, count=3)
        assert "- 하린: line7" in text and "- unknown: line8" in text and "line5" not in text
        assert "do not translate them again" in text
        assert previous_lines([]) == ""

    def test_a_question_mark_speaker_is_not_a_name(self):
        """A real model answered "???" for a speaker it could not place."""
        assert speakers_so_far([_blk(speaker="???", gender="male"), _blk(speaker="…")]) == ""


class TestStripSheet:
    def test_a_short_page_is_one_column(self):
        assert column_count(1800, 1000) == 1
        sheet, columns = strip_sheet(np.zeros((1800, 1000, 3), np.uint8))
        assert columns == 1 and max(sheet.shape[:2]) <= 2048
        assert sheet_note(1) == ""

    def test_a_long_strip_folds_into_a_roughly_square_sheet(self):
        strip = np.zeros((25000, 1000, 3), np.uint8)
        sheet, columns = strip_sheet(strip)
        assert columns == 5
        h, w = sheet.shape[:2]
        assert max(h, w) <= 2048 and 0.6 < w / h < 1.6
        assert "5 columns" in sheet_note(columns)

    def test_columns_run_left_to_right_top_to_bottom(self):
        strip = np.zeros((9000, 1000, 3), np.uint8)
        for i in range(9):  # a band per 1000 rows, brightness rising
            strip[i * 1000:(i + 1) * 1000] = 20 + i * 25
        sheet, columns = strip_sheet(strip, max_side=100000)
        assert columns == 3
        col_w = 1000 + 16
        first = [int(sheet[10, c * col_w + 500, 0]) for c in range(3)]
        assert first == sorted(first)                 # each column starts lower down the strip
        assert int(sheet[10, 500, 0]) == 20           # the strip's top opens the first column


class _Translator:
    calls = []

    def __init__(self, main_page, source_lang, target_lang):
        self.is_llm_engine = False
        self.target_lang_en = target_lang

    def translate(self, blocks, image, extra_context):
        _Translator.calls.append((len(blocks), image.shape, extra_context))
        if _Translator.fail_at == len(_Translator.calls):
            raise RuntimeError("boom")
        for blk in blocks:
            blk.translation = "ไปครับ"
            blk.speaker, blk.speaker_gender = "ไค", "male"

    def check_glossary(self, blocks):
        from modules.translation.processor import Translator
        return Translator.check_glossary(self, blocks)


@pytest.fixture
def chunk_proc(monkeypatch):
    from pipeline.webtoon_batch import chunk

    _Translator.calls = []
    _Translator.fail_at = 0
    monkeypatch.setattr(chunk, "Translator", _Translator)
    events = {"skipped": [], "issues": []}
    main = types.SimpleNamespace(
        settings_page=types.SimpleNamespace(get_extra_context=lambda text: "GLOSSARY"),
        image_skipped=types.SimpleNamespace(emit=lambda *a: events["skipped"].append(a)),
        glossary_issues_found=types.SimpleNamespace(emit=lambda *a: events["issues"].append(a)),
    )
    proc = types.SimpleNamespace(main_page=main, events=events)
    proc._extract_error_message = lambda error, context: str(error)
    for name in ("_run_translation_on_blocks", "_run_translation_on_strip"):
        method = getattr(chunk.ChunkMixin, name)
        setattr(proc, name, types.MethodType(method, proc))
    proc._assemble_strip = chunk.ChunkMixin._assemble_strip
    return proc, chunk


class TestStripTranslation:
    def test_a_long_strip_is_sent_in_groups_that_know_the_earlier_speakers(self, chunk_proc):
        proc, chunk = chunk_proc
        blocks = [_blk(f"t{i}") for i in range(2 * chunk.STRIP_GROUP_BLOCKS + 5)]
        proc._run_translation_on_blocks(np.zeros((8, 8, 3), np.uint8), blocks, "Korean", "Thai",
                                        "/w/1.png", image_note="NOTE")
        sizes = [c[0] for c in _Translator.calls]
        assert sizes == [chunk.STRIP_GROUP_BLOCKS, chunk.STRIP_GROUP_BLOCKS, 5]
        first, second = _Translator.calls[0][2], _Translator.calls[1][2]
        assert first.startswith("GLOSSARY") and "NOTE" in first and "ไค" not in first
        assert "ไค (male)" in second
        assert all(b.translation == "ไปครับ" for b in blocks)
        assert proc.events["issues"] == []          # male speaker, ครับ: nothing to check

    def test_a_failed_group_blanks_only_its_own_blocks(self, chunk_proc):
        proc, chunk = chunk_proc
        _Translator.fail_at = 2
        blocks = [_blk(f"t{i}") for i in range(chunk.STRIP_GROUP_BLOCKS + 3)]
        proc._run_translation_on_blocks(np.zeros((8, 8, 3), np.uint8), blocks, "Korean", "Thai", "/w/1.png")
        assert [b.translation for b in blocks[:chunk.STRIP_GROUP_BLOCKS]] == ["ไปครับ"] * chunk.STRIP_GROUP_BLOCKS
        assert [b.translation for b in blocks[chunk.STRIP_GROUP_BLOCKS:]] == [""] * 3
        assert proc.events["skipped"] == [("/w/1.png", "Translation", "boom")]

    def test_a_strip_is_one_request_in_reading_order_with_the_folded_image(self, chunk_proc):
        proc, _chunk = chunk_proc

        def job(top, height, ys):
            blocks = [TextBlock(text_bbox=np.array([10, y, 50, y + 20]), text=f"y{top + y}") for y in ys]
            return {
                "current_record": {"vpage": types.SimpleNamespace(crop_top=top),
                                   "image": np.full((height, 800, 3), 200, np.uint8)},
                "ocr_blocks": blocks, "source_lang": "Korean", "target_lang": "Thai",
            }

        jobs = [job(0, 2400, [900, 100]), job(2400, 2400, [50, 1500]), job(4800, 2400, [10])]
        # Paint each strip row band with its own grey so a crop can be traced back.
        for j in jobs:
            image = j["current_record"]["image"]
            top = j["current_record"]["vpage"].crop_top
            for y in range(0, image.shape[0], 100):
                image[y:y + 100] = ((top + y) // 100) % 250
        seen = []
        proc._run_translation_on_blocks = lambda **kw: seen.append(kw)
        proc._run_translation_on_strip("/w/1.png", jobs)
        (call,) = seen
        assert [b.text for b in call["blocks"]] == ["y100", "y900", "y2450", "y3900", "y4810"]

        whole, note = call["group_image"](0, 5)   # rows 100..4830, padded by 400
        assert max(whole.shape[:2]) <= 2048 and "3 columns" in note
        assert _chunk.STRIP_PART_NOTE in note      # rows past 5230 are not shown

        assert _chunk.STRIP_COVERED_NOTE not in note  # nothing of another group was covered

        part, part_note = call["group_image"](2, 4)  # rows 2450..3920 only
        assert _chunk.STRIP_PART_NOTE in part_note
        top_grey = int(part[0, 0, 0])
        assert top_grey == (2450 - 400) // 100 % 250  # the crop starts just above its first block

    def test_other_groups_lettering_is_covered_in_a_groups_image(self, chunk_proc, monkeypatch):
        """A neighbour's bubble at the edge of the image shifted a real model's
        translations by one block, so only this group's lettering stays readable."""
        proc, chunk = chunk_proc
        monkeypatch.setattr(chunk, "STRIP_GROUP_BLOCKS", 1)
        blocks = [TextBlock(text_bbox=np.array([100, y, 300, y + 100]), text=f"b{y}") for y in (500, 800)]
        jobs = [{"current_record": {"vpage": types.SimpleNamespace(crop_top=0),
                                    "image": np.zeros((2400, 800, 3), np.uint8)},
                 "ocr_blocks": blocks, "source_lang": "Korean", "target_lang": "Thai"}]
        seen = []
        proc._run_translation_on_blocks = lambda **kw: seen.append(kw)
        proc._run_translation_on_strip("/w/1.png", jobs)
        sheet, note = seen[0]["group_image"](0, 1)   # rows 100..1000: both blocks show
        assert sheet.shape[:2] == (900, 800) and chunk.STRIP_COVERED_NOTE in note
        assert (sheet[400:500, 100:300] == 0).all()                   # its own block: untouched
        assert (sheet[700:800, 100:300] == chunk.OTHER_GROUP_FILL).all()  # the other: covered
        assert (sheet[700:800, 400:] == 0).all()                       # art beside it: untouched

    def test_one_group_per_request_gets_its_own_image(self, chunk_proc):
        """Measured on a real model: a group shown the whole strip translated
        the bubbles it read in the picture, not the ones it was sent."""
        proc, chunk = chunk_proc
        proc._run_translation_on_blocks(
            None, [_blk(f"t{i}") for i in range(chunk.STRIP_GROUP_BLOCKS + 3)], "Korean", "Thai",
            "/w/1.png", group_image=lambda a, b: (np.full((4, 4, 3), a, np.uint8), f"part {a}-{b}"),
        )
        assert [(c[0], c[1]) for c in _Translator.calls] == [
            (chunk.STRIP_GROUP_BLOCKS, (4, 4, 3)), (3, (4, 4, 3))]
        assert "part 0-40" in _Translator.calls[0][2] and "part 40-43" in _Translator.calls[1][2]


class TestFlowWaitsForTheWholeStrip:
    def test_cleaning_runs_after_the_strip_is_translated(self):
        """Inpainting skips blocks with no translation, so the order matters."""
        from pipeline.webtoon_batch.flow import FlowMixin

        order = []
        block = TextBlock(text_bbox=np.array([0, 0, 10, 10]), text="가")
        record = {"image": np.zeros((4, 4, 3), np.uint8), "global_index": 0, "path": "/w/1.png",
                  "y_offset": 0, "vpage": object()}
        job = {"current_record": record, "next_record": None, "regular_blocks": [block],
               "split_owned_blocks": [], "split_matches": [], "ocr_blocks": [block],
               "source_lang": "Korean", "target_lang": "Thai"}

        def translate(path, jobs):
            order.append("translate")
            block.translation = "ไป"

        def inpaint(image, blocks):
            order.append(("inpaint", [b.translation for b in blocks]))
            return None, None

        proc = types.SimpleNamespace(
            _emit_progress=lambda *a: None,
            _run_translation_on_strip=translate,
            _inpaint_image_with_blocks=inpaint,
            _convert_blocks_to_physical=lambda blocks, vpage: [b.deep_copy() for b in blocks],
        )
        accum = {"/w/1.png": {"blocks": [], "patches": []}}
        FlowMixin._complete_strip_jobs(proc, {"path": "/w/1.png", "selected_index": 0}, [job], accum, 1)
        assert order == ["translate", ("inpaint", ["ไป"])]
        assert [b.translation for b in accum["/w/1.png"]["blocks"]] == ["ไป"]
        assert record["image"] is None   # the strip's chunk images are let go


def test_the_cache_keeps_the_speaker():
    from pipeline.cache_manager import CacheManager

    cache = CacheManager()
    blk = TextBlock(text_bbox=np.array([0, 0, 10, 10]), text="가", translation="ไปครับ")
    blk.speaker, blk.speaker_gender = "ไค", "male"
    cache._cache_translation_results("k", [blk])
    fresh = TextBlock(text_bbox=np.array([0, 0, 10, 10]), text="가")
    cache._apply_cached_translations_to_blocks("k", [fresh])
    assert (fresh.translation, fresh.speaker, fresh.speaker_gender) == ("ไปครับ", "ไค", "male")


def test_the_real_webtoon_loop_translates_each_strip_once(tmp_path, monkeypatch):
    """webtoon_batch_process end to end, with the models stubbed: every chunk
    of a strip is OCR'd, the strip goes out as one translation, and cleaning
    only starts once that translation is in."""
    from PIL import Image

    from pipeline.webtoon_batch import chunk
    from pipeline.webtoon_batch.processor import WebtoonBatchProcessor

    paths = []
    for name in ("001.png", "002.png"):
        path = tmp_path / name
        Image.new("RGB", (400, 7200), "white").save(path)
        paths.append(str(path))

    calls = []

    class FakeTranslator:
        def __init__(self, main_page, source_lang, target_lang):
            self.is_llm_engine = False
            self.target_lang_en = target_lang

        def translate(self, blocks, image, extra_context):
            calls.append(("translate", [b.text for b in blocks], image.shape))
            for blk in blocks:
                blk.translation = "แปล:" + blk.text
                blk.speaker, blk.speaker_gender = "ลีน่า", "female"

        def check_glossary(self, blocks):
            from modules.translation.processor import Translator
            return Translator.check_glossary(self, blocks)

    monkeypatch.setattr(chunk, "Translator", FakeTranslator)

    def emitter(name):
        return types.SimpleNamespace(emit=lambda *a: calls.append((name, a)))

    states = {p: {"source_lang": "Korean", "target_lang": "Thai"} for p in paths}
    main = types.SimpleNamespace(
        image_files=paths,
        image_states=types.SimpleNamespace(
            get_page_state=lambda path, default=None: states.get(path, default),
            ensure_page=lambda path: states.setdefault(path, {}),
        ),
        progress_update=emitter("progress"),
        image_skipped=emitter("skipped"),
        glossary_issues_found=emitter("issues"),
        render_state_ready=emitter("ready"),
        patches_processed=emitter("patches"),
        file_handler=types.SimpleNamespace(should_pre_materialize=lambda paths: False),
        current_worker=None,
        settings_page=types.SimpleNamespace(get_extra_context=lambda text: ""),
        s_combo=types.SimpleNamespace(currentText=lambda: "Korean"),
        t_combo=types.SimpleNamespace(currentText=lambda: "Thai"),
        lang_mapping={},
    )
    proc = WebtoonBatchProcessor(main, None, None, None, None)

    def detect(image):
        return [TextBlock(text_bbox=np.array([50, 1000, 300, 1100]), text_class="text_bubble")]

    def ocr(image, blocks, source_lang, paths_, reason, sort_after):
        for blk in blocks:
            blk.text = f"ocr{len([c for c in calls if c[0] == 'ocr'])}"
            calls.append(("ocr", blk.text))
        return blocks

    def inpaint(image, blocks):
        calls.append(("inpaint", [b.translation for b in blocks]))
        return None, None

    stored = {}
    proc._detect_blocks_for_page = detect
    proc._run_ocr_on_blocks = ocr
    proc._inpaint_image_with_blocks = inpaint
    proc._prepare_page_blocks_for_render = lambda image_path, blocks, has_patches: blocks
    proc._store_page_text_items = lambda page_index, image_path, blocks, image_shape: stored.__setitem__(
        image_path, [(b.translation, b.speaker) for b in blocks])
    proc._save_final_rendered_page = lambda *a: None

    proc.webtoon_batch_process(paths)

    kinds = [c[0] for c in calls if c[0] in ("ocr", "translate", "inpaint")]
    # Three chunks per strip: three OCRs, one translation, then three cleanings.
    assert kinds == ["ocr"] * 3 + ["translate"] + ["inpaint"] * 3 + \
                    ["ocr"] * 3 + ["translate"] + ["inpaint"] * 3
    translations = [c for c in calls if c[0] == "translate"]
    assert translations[0][1] == ["ocr0", "ocr1", "ocr2"]
    assert translations[1][1] == ["ocr3", "ocr4", "ocr5"]
    assert all(c[1] and c[1][0].startswith("แปล:") for c in calls if c[0] == "inpaint")
    assert stored[paths[0]] == [("แปล:ocr0", "ลีน่า"), ("แปล:ocr1", "ลีน่า"), ("แปล:ocr2", "ลีน่า")]
    assert not [c for c in calls if c[0] == "skipped"]
