import json
import math
import random
from array import array
from pathlib import Path

import pygame


GRID_SIZE = 16
INSTRUMENTS = [
    {
        "name": "Bell",
        "waveform": "sine",
        "cell_on": (86, 190, 255),
        "cell_on_playhead": (255, 223, 102),
        "tab": (58, 100, 144),
    },
    {
        "name": "Pluck",
        "waveform": "triangle",
        "cell_on": (125, 224, 163),
        "cell_on_playhead": (255, 240, 153),
        "tab": (54, 122, 89),
    },
    {
        "name": "Reed",
        "waveform": "square",
        "cell_on": (255, 154, 109),
        "cell_on_playhead": (255, 226, 160),
        "tab": (146, 86, 61),
    },
]

WINDOW_WIDTH = 920
WINDOW_HEIGHT = 820
DEFAULT_BPM = 132
MIN_BPM = 40
MAX_BPM = 320
APP_DIR = Path(__file__).resolve().parent
SAVE_FILE = APP_DIR / "tone_matrix_pattern.json"

BACKGROUND_COLOR = (12, 14, 18)
GRID_BG_COLOR = (18, 21, 28)
GRID_LINE_COLOR = (42, 47, 58)
CELL_OFF_COLOR = (38, 48, 63)
PLAYHEAD_COLOR = (255, 246, 180)
TEXT_COLOR = (215, 221, 232)
MUTED_TEXT_COLOR = (135, 145, 162)
STATUS_GOOD_COLOR = (138, 226, 164)
STATUS_WARN_COLOR = (255, 188, 100)
TAB_IDLE_COLOR = (33, 39, 49)
TAB_BORDER_COLOR = (60, 68, 82)

SAMPLE_RATE = 44100
NOTE_DURATION = 0.36
ATTACK_TIME = 0.008
RELEASE_TIME = 0.06
MASTER_VOLUME = 0.18


class Grid:
    def __init__(self, rows, cols):
        self.rows = rows
        self.cols = cols
        self.cells = [[False for _ in range(cols)] for _ in range(rows)]

    def set_cell(self, row, col, value):
        self.cells[row][col] = bool(value)

    def get_cell(self, row, col):
        return self.cells[row][col]

    def clear(self):
        for row in range(self.rows):
            for col in range(self.cols):
                self.cells[row][col] = False

    def randomize(self, density=0.26):
        for row in range(self.rows):
            for col in range(self.cols):
                self.cells[row][col] = random.random() < density

    def active_rows_in_column(self, col):
        return [row for row in range(self.rows) if self.cells[row][col]]

    def active_count(self):
        return sum(1 for row in self.cells for cell in row if cell)

    def serialize(self):
        return [[int(cell) for cell in row] for row in self.cells]

    def deserialize(self, rows_data):
        if len(rows_data) != self.rows:
            raise ValueError("Pattern row count does not match grid size.")
        for row_index, row_data in enumerate(rows_data):
            if len(row_data) != self.cols:
                raise ValueError("Pattern column count does not match grid size.")
            for col_index, value in enumerate(row_data):
                self.cells[row_index][col_index] = bool(value)


class InstrumentTrack:
    def __init__(self, config, frequencies, channel_offset):
        self.name = config["name"]
        self.waveform = config["waveform"]
        self.cell_on_color = config["cell_on"]
        self.cell_on_playhead_color = config["cell_on_playhead"]
        self.tab_color = config["tab"]
        self.grid = Grid(GRID_SIZE, GRID_SIZE)
        self.synth = Synth(frequencies, self.waveform, channel_offset)


class Synth:
    def __init__(self, frequencies, waveform, channel_offset):
        self.waveform = waveform
        self.channels = [
            pygame.mixer.Channel(channel_offset + index)
            for index in range(len(frequencies))
        ]
        self.sounds = {
            row: self._generate_sound(frequency)
            for row, frequency in enumerate(frequencies)
        }

    def _wave_sample(self, phase):
        sine = math.sin(phase)
        if self.waveform == "triangle":
            return (2.0 / math.pi) * math.asin(sine)
        if self.waveform == "square":
            overtone = math.sin(phase * 3.0) / 3.0 + math.sin(phase * 5.0) / 5.0
            return 0.82 * sine + 0.28 * overtone
        return sine + 0.18 * math.sin(phase * 2.0)

    def _generate_sound(self, frequency):
        sample_count = max(1, int(SAMPLE_RATE * NOTE_DURATION))
        attack_samples = max(1, int(SAMPLE_RATE * ATTACK_TIME))
        release_samples = max(1, int(SAMPLE_RATE * RELEASE_TIME))
        peak = int(32767 * MASTER_VOLUME)
        buffer = array("h")

        for i in range(sample_count):
            t = i / SAMPLE_RATE
            envelope = 1.0
            if i < attack_samples:
                envelope = i / attack_samples
            elif i >= sample_count - release_samples:
                envelope = max(0.0, (sample_count - 1 - i) / release_samples)

            phase = 2.0 * math.pi * frequency * t
            value = int(self._wave_sample(phase) * envelope * peak)
            buffer.append(value)
            buffer.append(value)

        return pygame.mixer.Sound(buffer=buffer.tobytes())

    def play_rows(self, rows):
        for row in rows:
            channel = self.channels[row]
            channel.stop()
            channel.play(self.sounds[row])

    def stop_all(self):
        for channel in self.channels:
            channel.stop()


class Sequencer:
    def __init__(self, tracks, bpm=DEFAULT_BPM):
        self.tracks = tracks
        self.bpm = bpm
        self.is_playing = True
        self.playhead = 0
        self.accumulator = 0.0

    @property
    def seconds_per_step(self):
        return 60.0 / self.bpm / 4.0

    def set_bpm(self, bpm):
        self.bpm = max(MIN_BPM, min(MAX_BPM, int(bpm)))

    def adjust_bpm(self, delta):
        self.set_bpm(self.bpm + delta)

    def stop_all(self):
        for track in self.tracks:
            track.synth.stop_all()

    def toggle_playback(self):
        self.is_playing = not self.is_playing
        self.accumulator = 0.0
        if not self.is_playing:
            self.stop_all()

    def update(self, dt):
        if not self.is_playing:
            return

        self.accumulator += dt
        while self.accumulator >= self.seconds_per_step:
            self.accumulator -= self.seconds_per_step
            self.trigger_current_column()
            self.playhead = (self.playhead + 1) % GRID_SIZE

    def trigger_current_column(self):
        for track in self.tracks:
            rows = track.grid.active_rows_in_column(self.playhead)
            if rows:
                track.synth.play_rows(rows)


class App:
    def __init__(self):
        pygame.mixer.pre_init(SAMPLE_RATE, size=-16, channels=2, buffer=512)
        pygame.init()
        pygame.display.set_caption("Tone Matrix")
        self.screen = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT))
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont("consolas", 20)
        self.small_font = pygame.font.SysFont("consolas", 16)

        self.grid_rect = self._build_grid_rect()
        self.cell_gap = 4
        self.cell_size = self._compute_cell_size()
        self.tab_rects = []
        self.dragging = False
        self.drag_value = True
        self.last_dragged_cell = None

        frequencies = self._build_scale_frequencies(GRID_SIZE)
        pygame.mixer.set_num_channels(len(INSTRUMENTS) * GRID_SIZE + 8)
        self.tracks = []
        for index, config in enumerate(INSTRUMENTS):
            self.tracks.append(InstrumentTrack(config, frequencies, index * GRID_SIZE))

        self.selected_track_index = 0
        self.sequencer = Sequencer(self.tracks, DEFAULT_BPM)
        self.status_text = "Ready"
        self.status_color = STATUS_GOOD_COLOR
        self.status_timer = 0.0

    @property
    def selected_track(self):
        return self.tracks[self.selected_track_index]

    def _build_grid_rect(self):
        margin_x = 70
        margin_top = 170
        margin_bottom = 170
        size = min(
            WINDOW_WIDTH - margin_x * 2, WINDOW_HEIGHT - margin_top - margin_bottom
        )
        return pygame.Rect((WINDOW_WIDTH - size) // 2, margin_top, size, size)

    def _compute_cell_size(self):
        return (self.grid_rect.width - self.cell_gap * (GRID_SIZE + 1)) / GRID_SIZE

    def _build_scale_frequencies(self, count):
        scale = [0, 3, 5, 7, 10]
        base_midi = 45
        midi_values = []
        for index in range(count):
            octave = index // len(scale)
            note = scale[index % len(scale)]
            midi_values.append(base_midi + octave * 12 + note)

        midi_values.reverse()
        return [440.0 * (2.0 ** ((midi - 69) / 12.0)) for midi in midi_values]

    def set_status(self, text, color=STATUS_GOOD_COLOR, duration=2.5):
        self.status_text = text
        self.status_color = color
        self.status_timer = duration

    def cell_from_pos(self, pos):
        if not self.grid_rect.collidepoint(pos):
            return None

        px, py = pos
        local_x = px - self.grid_rect.x - self.cell_gap
        local_y = py - self.grid_rect.y - self.cell_gap
        pitch = self.cell_size + self.cell_gap
        col = int(local_x // pitch)
        row = int(local_y // pitch)

        if not (0 <= row < GRID_SIZE and 0 <= col < GRID_SIZE):
            return None

        cell_x = self.grid_rect.x + self.cell_gap + col * pitch
        cell_y = self.grid_rect.y + self.cell_gap + row * pitch
        rect = pygame.Rect(
            round(cell_x), round(cell_y), round(self.cell_size), round(self.cell_size)
        )
        if rect.collidepoint(pos):
            return row, col
        return None

    def tab_from_pos(self, pos):
        for index, rect in enumerate(self.tab_rects):
            if rect.collidepoint(pos):
                return index
        return None

    def handle_mouse_down(self, pos):
        tab_index = self.tab_from_pos(pos)
        if tab_index is not None:
            self.selected_track_index = tab_index
            return

        cell = self.cell_from_pos(pos)
        if cell is None:
            return

        row, col = cell
        self.dragging = True
        self.drag_value = not self.selected_track.grid.get_cell(row, col)
        self.selected_track.grid.set_cell(row, col, self.drag_value)
        self.last_dragged_cell = cell

    def handle_mouse_drag(self, pos):
        if not self.dragging:
            return
        cell = self.cell_from_pos(pos)
        if cell is None or cell == self.last_dragged_cell:
            return
        row, col = cell
        self.selected_track.grid.set_cell(row, col, self.drag_value)
        self.last_dragged_cell = cell

    def handle_mouse_up(self):
        self.dragging = False
        self.last_dragged_cell = None

    def clear_selected_track(self):
        self.selected_track.grid.clear()
        self.set_status(f"Cleared {self.selected_track.name}", STATUS_WARN_COLOR)

    def randomize_selected_track(self):
        self.selected_track.grid.randomize()
        self.set_status(f"Randomized {self.selected_track.name}")

    def save_pattern(self):
        payload = {
            "grid_size": GRID_SIZE,
            "bpm": self.sequencer.bpm,
            "selected_track": self.selected_track_index,
            "tracks": [
                {
                    "name": track.name,
                    "waveform": track.waveform,
                    "cells": track.grid.serialize(),
                }
                for track in self.tracks
            ],
        }
        with SAVE_FILE.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        self.set_status(f"Saved to {SAVE_FILE.name}")

    def load_pattern(self):
        with SAVE_FILE.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        if payload.get("grid_size") != GRID_SIZE:
            raise ValueError("Saved pattern grid size does not match this app.")

        tracks_data = payload.get("tracks")
        if tracks_data is None:
            self.tracks[0].grid.deserialize(payload["cells"])
            for track in self.tracks[1:]:
                track.grid.clear()
        else:
            if len(tracks_data) != len(self.tracks):
                raise ValueError(
                    "Saved pattern instrument count does not match this app."
                )
            for track, track_data in zip(self.tracks, tracks_data):
                track.grid.deserialize(track_data["cells"])

        self.selected_track_index = max(
            0, min(len(self.tracks) - 1, int(payload.get("selected_track", 0)))
        )
        self.sequencer.set_bpm(payload.get("bpm", DEFAULT_BPM))
        self.set_status(f"Loaded {SAVE_FILE.name}")

    def draw_tabs(self):
        self.tab_rects = []
        gap = 12
        tab_width = 168
        tab_height = 56
        total_width = len(self.tracks) * tab_width + (len(self.tracks) - 1) * gap
        start_x = (WINDOW_WIDTH - total_width) // 2
        y = 90

        for index, track in enumerate(self.tracks):
            rect = pygame.Rect(
                start_x + index * (tab_width + gap), y, tab_width, tab_height
            )
            self.tab_rects.append(rect)
            fill = (
                track.tab_color
                if index == self.selected_track_index
                else TAB_IDLE_COLOR
            )
            pygame.draw.rect(self.screen, fill, rect, border_radius=12)
            pygame.draw.rect(
                self.screen, TAB_BORDER_COLOR, rect, width=2, border_radius=12
            )

            name = self.font.render(f"{index + 1}. {track.name}", True, TEXT_COLOR)
            count = self.small_font.render(
                f"{track.grid.active_count()} notes", True, MUTED_TEXT_COLOR
            )
            self.screen.blit(name, (rect.x + 12, rect.y + 8))
            self.screen.blit(count, (rect.x + 12, rect.y + 32))

    def draw_grid(self):
        track = self.selected_track
        self.screen.fill(BACKGROUND_COLOR)
        pygame.draw.rect(self.screen, GRID_BG_COLOR, self.grid_rect, border_radius=18)

        pitch = self.cell_size + self.cell_gap
        playhead_x = (
            self.grid_rect.x
            + self.cell_gap
            + self.sequencer.playhead * pitch
            - self.cell_gap * 0.5
        )
        playhead_rect = pygame.Rect(
            round(playhead_x),
            self.grid_rect.y + 6,
            round(self.cell_size + self.cell_gap),
            self.grid_rect.height - 12,
        )
        pygame.draw.rect(self.screen, PLAYHEAD_COLOR, playhead_rect, border_radius=14)

        for row in range(GRID_SIZE):
            for col in range(GRID_SIZE):
                x = self.grid_rect.x + self.cell_gap + col * pitch
                y = self.grid_rect.y + self.cell_gap + row * pitch
                rect = pygame.Rect(
                    round(x), round(y), round(self.cell_size), round(self.cell_size)
                )
                is_active = track.grid.get_cell(row, col)
                is_playhead = (
                    col == self.sequencer.playhead and self.sequencer.is_playing
                )

                if is_active and is_playhead:
                    color = track.cell_on_playhead_color
                elif is_active:
                    color = track.cell_on_color
                else:
                    color = CELL_OFF_COLOR

                pygame.draw.rect(self.screen, color, rect, border_radius=8)
                pygame.draw.rect(
                    self.screen, GRID_LINE_COLOR, rect, width=1, border_radius=8
                )

    def draw_ui(self):
        title = self.font.render("Tone Matrix", True, TEXT_COLOR)
        bpm_text = self.font.render(f"BPM: {self.sequencer.bpm}", True, TEXT_COLOR)
        state_label = "Playing" if self.sequencer.is_playing else "Paused"
        state_text = self.small_font.render(state_label, True, MUTED_TEXT_COLOR)
        instrument_text = self.small_font.render(
            f"Editing: {self.selected_track.name} ({self.selected_track.waveform})",
            True,
            MUTED_TEXT_COLOR,
        )
        controls = self.small_font.render(
            "Space play/pause   1/2/3 select instrument   C clear current   R randomize current",
            True,
            MUTED_TEXT_COLOR,
        )
        controls_two = self.small_font.render(
            "Up/Down BPM   S save   L load   Click tabs to switch instruments",
            True,
            MUTED_TEXT_COLOR,
        )
        save_hint = self.small_font.render(
            f"Pattern file: {SAVE_FILE.name}", True, MUTED_TEXT_COLOR
        )
        status = self.small_font.render(self.status_text, True, self.status_color)

        self.screen.blit(title, (self.grid_rect.x, 28))
        self.screen.blit(bpm_text, (self.grid_rect.right - bpm_text.get_width(), 28))
        self.screen.blit(state_text, (self.grid_rect.x, 56))
        self.screen.blit(instrument_text, (self.grid_rect.x + 90, 56))
        self.draw_tabs()
        self.screen.blit(controls, (self.grid_rect.x, self.grid_rect.bottom + 30))
        self.screen.blit(controls_two, (self.grid_rect.x, self.grid_rect.bottom + 56))
        self.screen.blit(save_hint, (self.grid_rect.x, self.grid_rect.bottom + 82))
        self.screen.blit(status, (self.grid_rect.x, self.grid_rect.bottom + 116))

    def draw(self):
        self.draw_grid()
        self.draw_ui()
        pygame.display.flip()

    def run(self):
        running = True
        while running:
            dt = self.clock.tick(120) / 1000.0
            if self.status_timer > 0.0:
                self.status_timer = max(0.0, self.status_timer - dt)
                if self.status_timer == 0.0:
                    self.status_text = "Ready"
                    self.status_color = STATUS_GOOD_COLOR

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    self.handle_mouse_down(event.pos)
                elif event.type == pygame.MOUSEMOTION and self.dragging:
                    self.handle_mouse_drag(event.pos)
                elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                    self.handle_mouse_up()
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_SPACE:
                        self.sequencer.toggle_playback()
                    elif event.key == pygame.K_c:
                        self.clear_selected_track()
                    elif event.key == pygame.K_r:
                        self.randomize_selected_track()
                    elif event.key == pygame.K_UP:
                        self.sequencer.adjust_bpm(4)
                        self.set_status(f"BPM {self.sequencer.bpm}")
                    elif event.key == pygame.K_DOWN:
                        self.sequencer.adjust_bpm(-4)
                        self.set_status(f"BPM {self.sequencer.bpm}")
                    elif event.key == pygame.K_s:
                        try:
                            self.save_pattern()
                        except OSError as exc:
                            self.set_status(
                                f"Save failed: {exc}", STATUS_WARN_COLOR, 4.0
                            )
                    elif event.key == pygame.K_l:
                        try:
                            self.load_pattern()
                        except (
                            OSError,
                            ValueError,
                            KeyError,
                            json.JSONDecodeError,
                        ) as exc:
                            self.set_status(
                                f"Load failed: {exc}", STATUS_WARN_COLOR, 4.0
                            )
                    elif pygame.K_1 <= event.key <= pygame.K_9:
                        index = event.key - pygame.K_1
                        if index < len(self.tracks):
                            self.selected_track_index = index

            self.sequencer.update(dt)
            self.draw()

        self.sequencer.stop_all()
        pygame.quit()


if __name__ == "__main__":
    App().run()
