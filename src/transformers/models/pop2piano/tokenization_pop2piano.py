# coding=utf-8
# Copyright 2023 The Pop2Piano Authors and The HuggingFace Inc. team.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Tokenization class for Pop2Piano."""

import json
import os
from typing import List, Optional, Tuple, Union

import numpy as np

from ...feature_extraction_utils import BatchFeature
from ...tokenization_utils import AddedToken, PreTrainedTokenizer
from ...utils import TensorType, is_pretty_midi_available, is_torch_tensor, logging, requires_backends, to_numpy


if is_pretty_midi_available():
    import pretty_midi
else:
    raise ModuleNotFoundError("pretty_midi was not found in your environment! Please use `pip install pretty_midi`")


logger = logging.get_logger(__name__)

VOCAB_FILES_NAMES = {
    "vocab_file": "vocab.json",
}

PRETRAINED_VOCAB_FILES_MAP = {
    "vocab_file": {
        "sweetcocoa/pop2piano": "https://huggingface.co/sweetcocoa/pop2piano/blob/main/vocab.json",
    },
}


def token_time_to_note(number, cutoff_time_idx, current_idx):
    current_idx += number
    if cutoff_time_idx is not None:
        current_idx = min(current_idx, cutoff_time_idx)

    return current_idx


def token_note_to_note(number, current_velocity, default_velocity, note_onsets_ready, current_idx, notes):
    if note_onsets_ready[number] is not None:
        # offset with onset
        onset_idx = note_onsets_ready[number]
        if onset_idx < current_idx:
            # Time shift after previous note_on
            offset_idx = current_idx
            notes.append([onset_idx, offset_idx, number, default_velocity])
            onsets_ready = None if current_velocity == 0 else current_idx
            note_onsets_ready[number] = onsets_ready
    else:
        note_onsets_ready[number] = current_idx
    return notes


class Pop2PianoTokenizer(PreTrainedTokenizer):
    """
    Constructs a Pop2Piano tokenizer. This tokenizer does not require training.

    This tokenizer inherits from [`PreTrainedTokenizer`] which contains most of the main methods. Users should refer
    to: this superclass for more information regarding those methods. However the code does not allow that and only
    supports composing from various genres.

    Args:
        vocab_file (`str`):
            Path to the tokenizer file which contains token-ids such as `TOKEN_SPECIAL`, `DEFAULT_VELOCITY`.
        vocab_size_special (`int`, *optional*, defaults to 4):
            Number of special values.
        vocab_size_note (`int`, *optional*, defaults to 128):
            Number of MIDI note tokens. Only the 88 pitches corresponding to piano keys are actually used.
        vocab_size_velocity (`int`, *optional*, defaults to 2):
            Number of velocity tokens.
        vocab_size_time (`int`, *optional*, defaults to 100):
            Number of beat shift tokens. Beat shifts indicate the relative time shift within the segment quantized into
            8th-notes (half-beats).
        num_bars (`int`, *optional*, defaults to 2):
            Determines cutoff_time_idx in for each token.
    """

    vocab_files_names = VOCAB_FILES_NAMES
    pretrained_vocab_files_map = PRETRAINED_VOCAB_FILES_MAP

    def __init__(
        self,
        vocab_file,
        unk_token="-1",
        eos_token="1",
        pad_token="0",
        bos_token="2",
        **kwargs,
    ):
        unk_token = AddedToken(unk_token, lstrip=False, rstrip=False) if isinstance(unk_token, str) else unk_token
        eos_token = AddedToken(eos_token, lstrip=False, rstrip=False) if isinstance(eos_token, str) else eos_token
        pad_token = AddedToken(pad_token, lstrip=False, rstrip=False) if isinstance(pad_token, str) else pad_token
        bos_token = AddedToken(bos_token, lstrip=False, rstrip=False) if isinstance(bos_token, str) else bos_token

        super().__init__(
            unk_token=unk_token,
            eos_token=eos_token,
            pad_token=pad_token,
            bos_token=bos_token,
            **kwargs,
        )

        with open(vocab_file, "rb") as t_file:
            self.encoder = json.load(t_file)

        self.vocab_size_special = self.encoder["vocab_size_special"]
        self.vocab_size_note = self.encoder["vocab_size_note"]
        self.vocab_size_velocity = self.encoder["vocab_size_velocity"]
        self.vocab_size_time = self.encoder["vocab_size_time"]
        self.num_bars = self.encoder["num_bars"]

    @property
    def vocab_size(self):
        """Returns the vocabulary size of the tokenizer."""
        return self.vocab_size_special + self.vocab_size_note + self.vocab_size_time + self.vocab_size_velocity

    def get_vocab(self):
        """Returns the vocabulary of the tokenizer."""
        return self.encoder

    # copied from the official pop2piano implementation with little modification
    # Please see https://github.com/sweetcocoa/pop2piano/blob/fac11e8dcfc73487513f4588e8d0c22a22f2fdc5/midi_tokenizer.py#L48
    def _convert_id_to_token(self, token: int, time_idx_offset: int):
        """
        Decodes the tokens generated by the transformer.

        Args:
            token (`int`):
                This denotes the token ids generated by the transformers to be converted to Midi tokens.
            time_idx_offset (`int`):
                This is a parameter which is used for TIME TOKEN when converting to Midi.
        """

        if token >= (self.vocab_size_special + self.vocab_size_note + self.vocab_size_velocity):
            token_type = self.encoder["TOKEN_TIME"]
            value = (
                token - (self.vocab_size_special + self.vocab_size_note + self.vocab_size_velocity)
            ) + time_idx_offset
        elif token >= (self.vocab_size_special + self.vocab_size_note):
            token_type = self.encoder["TOKEN_VELOCITY"]
            value = int(token - (self.vocab_size_special + self.vocab_size_note))
        elif token >= self.vocab_size_special:
            token_type = self.encoder["TOKEN_NOTE"]
            value = int(token - self.vocab_size_special)
        else:
            token_type = self.encoder["TOKEN_SPECIAL"]
            value = int(token)

        return [token_type, value]

    # copied from the official pop2piano implementation with little modification
    # Please see https://github.com/sweetcocoa/pop2piano/blob/fac11e8dcfc73487513f4588e8d0c22a22f2fdc5/midi_tokenizer.py#L34
    def _convert_token_to_id(self, token, token_type="3"):
        """
        Encodes the Midi tokens to transformer generated tokens.

        Args:
            token (`int`):
                This denotes the token value.
            token_type (`str`):
                This denotes the type of the token. There are four types of midi tokens such as "TIME", "VELOCITY",
                "NOTE", "SPECIAL".
        """
        if token_type == self.encoder["TOKEN_TIME"]:
            return self.vocab_size_special + self.vocab_size_note + self.vocab_size_velocity + token
        elif token_type == self.encoder["TOKEN_VELOCITY"]:
            return self.vocab_size_special + self.vocab_size_note + token
        elif token_type == self.encoder["TOKEN_NOTE"]:
            return self.vocab_size_special + token
        elif token_type == self.encoder["TOKEN_SPECIAL"]:
            return token
        else:
            return -1

    def relative_batch_tokens_to_notes(
        self,
        tokens: np.ndarray,
        beat_offset_idx: int,
        bars_per_batch: int,
        cutoff_time_idx: int,
    ):
        """
        Converts relative tokens to notes which are then used to generate pretty midi object.

        Args:
            tokens (`numpy.ndarray`):
                Tokens to be converted to notes.
            beat_offset_idx (`int`):
                Denotes beat offset index for each note in generated Midi.
            bars_per_batch (`int`):
                A parameter to control the Midi output generation.
            cutoff_time_idx (`int`):
                Denotes the cutoff time index for each note in generated Midi.
        """

        notes = None

        for index in range(len(tokens)):
            _tokens = tokens[index]
            _start_idx = beat_offset_idx + index * bars_per_batch * 4
            _cutoff_time_idx = cutoff_time_idx + _start_idx
            _notes = self.relative_tokens_to_notes(
                _tokens,
                start_idx=_start_idx,
                cutoff_time_idx=_cutoff_time_idx,
            )

            if len(_notes) == 0:
                pass
            elif notes is None:
                notes = _notes
            else:
                notes = np.concatenate((notes, _notes), axis=0)

        if notes is None:
            return []
        return notes

    def relative_batch_tokens_to_midi(
        self,
        tokens: np.ndarray,
        beatstep: np.ndarray,
        beat_offset_idx: int = 0,
        bars_per_batch: int = 2,
        cutoff_time_idx: int = 12,
    ):
        """
        Converts tokens to Midi. This method calls `relative_batch_tokens_to_notes` method to convert batch tokens to
        notes then uses `notes_to_midi` method to convert them to Midi.

        Args:
            tokens (`numpy.ndarray`):
                Denotes tokens which alongside beatstep will be converted to Midi.
            beatstep (`np.ndarray`):
                We get beatstep from feature extractor which is also used to get Midi.
            beat_offset_idx (`int`, *optional*, defaults to 0):
                Denotes beat offset index for each note in generated Midi.
            bars_per_batch (`int`, *optional*, defaults to 2):
                A parameter to control the Midi output generation.
            cutoff_time_idx (`int`, *optional*, defaults to 12):
                Denotes the cutoff time index for each note in generated Midi.
        """
        beat_offset_idx = 0 if beat_offset_idx is None else beat_offset_idx
        notes = self.relative_batch_tokens_to_notes(
            tokens=tokens,
            beat_offset_idx=beat_offset_idx,
            bars_per_batch=bars_per_batch,
            cutoff_time_idx=cutoff_time_idx,
        )
        midi = self.notes_to_midi(notes, beatstep, offset_sec=beatstep[beat_offset_idx])
        return midi

    # Taken from the original code
    # Please see https://github.com/sweetcocoa/pop2piano/blob/fac11e8dcfc73487513f4588e8d0c22a22f2fdc5/midi_tokenizer.py#L257
    def relative_tokens_to_notes(self, tokens: np.ndarray, start_idx: float, cutoff_time_idx: float = None):
        """
        Converts relative tokens to notes which will then be used to create Pretty Midi objects.

        Args:
            tokens (`numpy.ndarray`):
                Relative Tokens which will be converted to notes.
            start_idx (`float`):
                A parameter which denotes the starting index.
            cutoff_time_idx (`float`, *optional*):
                A parameter used while converting tokens to notes.
        """
        if tokens[0] >= (
            self.vocab_size_special + self.vocab_size_note + self.vocab_size_velocity + self.vocab_size_time
        ):
            tokens = tokens[1:]

        words = [self._convert_id_to_token(token, time_idx_offset=0) for token in tokens]

        if is_torch_tensor(start_idx):
            start_idx = start_idx.item()

        current_idx = start_idx
        current_velocity = 0
        note_onsets_ready = [None for i in range(self.vocab_size_note + 1)]
        notes = []
        for token_type, number in words:
            if token_type == self.encoder["TOKEN_SPECIAL"]:
                if number == 1:
                    break
            elif token_type == self.encoder["TOKEN_TIME"]:
                current_idx = token_time_to_note(
                    number=number, cutoff_time_idx=cutoff_time_idx, current_idx=current_idx
                )
            elif token_type == self.encoder["TOKEN_VELOCITY"]:
                current_velocity = number

            elif token_type == self.encoder["TOKEN_NOTE"]:
                notes = token_note_to_note(
                    number=number,
                    current_velocity=current_velocity,
                    default_velocity=self.encoder["DEFAULT_VELOCITY"],
                    note_onsets_ready=note_onsets_ready,
                    current_idx=current_idx,
                    notes=notes,
                )
            else:
                raise ValueError("Token type not understood!")

        for pitch, note_onset in enumerate(note_onsets_ready):
            # force offset if no offset for each pitch
            if note_onset is not None:
                if cutoff_time_idx is None:
                    cutoff = note_onset + 1
                else:
                    cutoff = max(cutoff_time_idx, note_onset + 1)

                offset_idx = max(current_idx, cutoff)
                notes.append([note_onset, offset_idx, pitch, self.encoder["DEFAULT_VELOCITY"]])

        if len(notes) == 0:
            return []
        else:
            notes = np.array(notes)
            note_order = notes[:, 0] * 128 + notes[:, 1]
            notes = notes[note_order.argsort()]
            return notes

    def notes_to_midi(self, notes: np.ndarray, beatstep: np.ndarray, offset_sec: int = 0.0):
        """
        Converts notes to Midi.

        Args:
            notes (`numpy.ndarray`):
                This is used to create Pretty Midi objects.
            beatstep (`numpy.ndarray`):
                This is the extrapolated beatstep that we get from feature extractor.
            offset_sec (`int`, *optional*, defaults to 0.0):
                This represents the offset seconds which is used while creating each Pretty Midi Note.
        """

        requires_backends(self, ["pretty_midi"])

        new_pm = pretty_midi.PrettyMIDI(resolution=384, initial_tempo=120.0)
        new_inst = pretty_midi.Instrument(program=0)
        new_notes = []

        for onset_idx, offset_idx, pitch, velocity in notes:
            new_note = pretty_midi.Note(
                velocity=velocity,
                pitch=pitch,
                start=beatstep[onset_idx] - offset_sec,
                end=beatstep[offset_idx] - offset_sec,
            )
            new_notes.append(new_note)
        new_inst.notes = new_notes
        new_pm.instruments.append(new_inst)
        new_pm.remove_invalid_notes()
        return new_pm

    def save_vocabulary(self, save_directory: str, filename_prefix: Optional[str] = None) -> Tuple[str]:
        """
        Saves the tokenizer's vocabulary dictionary to the provided save_directory.

        Args:
            save_directory (`str`):
                A path to the directory where to saved. It will be created if it doesn't exist.
            filename_prefix (`Optional[str]`, *optional*):
                A prefix to add to the names of the files saved by the tokenizer.
        """
        if not os.path.isdir(save_directory):
            logger.error(f"Vocabulary path ({save_directory}) should be a directory")
            return

        vocab_file = os.path.join(
            save_directory, (filename_prefix + "-" if filename_prefix else "") + VOCAB_FILES_NAMES["vocab_file"]
        )
        with open(vocab_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(self.encoder))

        return (vocab_file,)

    def __call__(
        self,
        token_ids: Union[List, TensorType],
        feature_extractor_output: BatchFeature,
        return_midi: bool = True,
    ):
        r"""
        This is the `__call__` method for `Pop2PianoTokenizer`. It converts the tokens generated by the transformer to
        midi_tokens and returns them.

        Args:
            token_ids (`torch.LongTensor`):
                Output tokens of `Pop2PianoConditionalGeneration` model.
            feature_extractor_output (`BatchFeature`):
                Denotes the output of `Pop2PianoFeatureExtractor.__call__`.
            return_midi (`bool`, *optional*, defaults to `True`):
                Whether to return midi object or not.
        Returns:
            If `return_midi` is True:
                - `BatchFeature` containing both `notes` and `pretty_midi.pretty_midi.PrettyMIDI` objects.
            If `return_midi` is False:
                - `BatchFeature` containing `notes`.
        """

        # check if they have attention_masks(attention_mask, attention_mask_beatsteps, attention_mask_extrapolated_beatstep) or not
        attention_masks_present = bool(
            hasattr(feature_extractor_output, "attention_mask")
            and hasattr(feature_extractor_output, "attention_mask_beatsteps")
            and hasattr(feature_extractor_output, "attention_mask_extrapolated_beatstep")
        )

        # if we are processing batched inputs then we must need attention_masks
        if not attention_masks_present and feature_extractor_output["beatsteps"].shape[0] > 1:
            raise ValueError(
                "attention_mask, attention_mask_beatsteps and attention_mask_extrapolated_beatstep must be present for batched inputs! But one of them were not present."
            )

        # check for length mismatch between inputs_embeds, beatsteps and extrapolated_beatstep
        if attention_masks_present:
            # since we know about the number of examples in token_ids from attention_mask
            if (
                sum(feature_extractor_output["attention_mask"][:, 0] == 0)
                != feature_extractor_output["beatsteps"].shape[0]
                or feature_extractor_output["beatsteps"].shape[0]
                != feature_extractor_output["extrapolated_beatstep"].shape[0]
            ):
                raise ValueError(
                    "Length mistamtch between token_ids, beatsteps and extrapolated_beatstep! Found "
                    f"token_ids length - {token_ids.shape[0]}, beatsteps shape - {feature_extractor_output['beatsteps'].shape[0]} "
                    f"and extrapolated_beatsteps shape - {feature_extractor_output['extrapolated_beatstep'].shape[0]}"
                )
            if feature_extractor_output["attention_mask"].shape[0] != token_ids.shape[0]:
                raise ValueError(
                    f"Found attention_mask of length - {feature_extractor_output['attention_mask'].shape[0]} but token_ids of length - {token_ids.shape[0]}"
                )
        else:
            # if there is no attention mask present then it's surely a single example
            if (
                feature_extractor_output["beatsteps"].shape[0] != 1
                or feature_extractor_output["extrapolated_beatstep"].shape[0] != 1
            ):
                raise ValueError(
                    "Length mistamtch of beatsteps and extrapolated_beatstep! Since attention_mask is not present the number of examples must be 1, "
                    f"But found beatsteps length - {feature_extractor_output['beatsteps'].shape[0]}, extrapolated_beatsteps length - {feature_extractor_output['extrapolated_beatstep'].shape[0]}."
                )

        if attention_masks_present:
            # check for zeros(since token_ids are seperated by zero arrays)
            batch_idx = np.where(feature_extractor_output["attention_mask"][:, 0] == 0)[0]
        else:
            batch_idx = [token_ids.shape[0]]

        notes_list = []
        pretty_midi_objects_list = []
        start_idx = 0
        for index, end_idx in enumerate(batch_idx):
            each_tokens_ids = token_ids[start_idx:end_idx]
            # check where the whole example ended by searching for eos_token_id and getting the upper bound
            each_tokens_ids = each_tokens_ids[:, : np.max(np.where(each_tokens_ids == int(self.eos_token))[1]) + 1]
            beatsteps = feature_extractor_output["beatsteps"][index]
            extrapolated_beatstep = feature_extractor_output["extrapolated_beatstep"][index]

            # if attention mask is present then mask out real array/tensor
            if attention_masks_present:
                attention_mask_beatsteps = feature_extractor_output["attention_mask_beatsteps"][index]
                attention_mask_extrapolated_beatstep = feature_extractor_output[
                    "attention_mask_extrapolated_beatstep"
                ][index]
                beatsteps = beatsteps[: np.max(np.where(attention_mask_beatsteps == 1)[0]) + 1]
                extrapolated_beatstep = extrapolated_beatstep[
                    : np.max(np.where(attention_mask_extrapolated_beatstep == 1)[0]) + 1
                ]

            each_tokens_ids = to_numpy(each_tokens_ids)
            beatsteps = to_numpy(beatsteps)
            extrapolated_beatstep = to_numpy(extrapolated_beatstep)

            pretty_midi_object = self.relative_batch_tokens_to_midi(
                tokens=each_tokens_ids,
                beatstep=extrapolated_beatstep,
                bars_per_batch=self.num_bars,
                cutoff_time_idx=(self.num_bars + 1) * 4,
            )

            for note in pretty_midi_object.instruments[0].notes:
                note.start += beatsteps[0]
                note.end += beatsteps[0]
                notes_list.append(note)

            pretty_midi_objects_list.append(pretty_midi_object)
            start_idx += end_idx + 1  # 1 represents the zero array

        if return_midi:
            return BatchFeature({"notes": notes_list, "pretty_midi_objects": pretty_midi_objects_list})

        return BatchFeature({"notes": notes_list})
