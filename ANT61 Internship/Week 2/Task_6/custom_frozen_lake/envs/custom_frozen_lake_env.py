from contextlib import closing
from io import StringIO
from os import path
from typing import Optional
from xml import dom
import pygame
from pygame.constants import SRCALPHA
import numpy as np

from gym import Env, spaces, utils
from gym.envs.toy_text.utils import categorical_sample

LEFT = 0
DOWN = 1
RIGHT = 2
UP = 3

MAPS = {
    "4x4": ["SFFF", "FHFH", "FFFH", "HFFG"],
    "8x8": [
        "SFFFFFFF",
        "FFFFFFFF",
        "FFFHFFFF",
        "FFFFFHFF",
        "FFFHFFFF",
        "FHHFFFHF",
        "FHFFHFHF",
        "FFFHFFFG",
    ],
}


def generate_random_map(size=8, p=0.8):
    """Generates a random valid map (one that has a path from start to goal)
    :param size: size of each side of the grid
    :param p: probability that a tile is frozen
    """
    valid = False

    # DFS to check that it's a valid path.
    def is_valid(res, start: tuple):
        frontier, discovered = [], set()
        frontier.append(start)
        while frontier:
            r, c = frontier.pop()
            if not (r, c) in discovered:
                discovered.add((r, c))
                directions = [(1, 0), (0, 1), (-1, 0), (0, -1)]
                for x, y in directions:
                    r_new = r + x
                    c_new = c + y
                    if r_new < 0 or r_new >= size or c_new < 0 or c_new >= size:
                        continue
                    if res[r_new][c_new] == "G":
                        return True
                    if res[r_new][c_new] != "H":
                        frontier.append((r_new, c_new))
        return False

    while not valid:
        p = min(1, p)
        res = np.random.choice(["F", "H"], (size, size), p=[p, 1 - p])
        # Generate random start/goal locations
        start = (np.random.randint(size), np.random.randint(size))
        goal = (np.random.randint(size), np.random.randint(size))
        # Make sure goal is not generated in the same place as start
        while goal == start:
            goal = (np.random.randint(size), np.random.randint(size))
        res[start] = "S"
        res[goal] = "G"
        valid = is_valid(res, start)
    # print(start)
    # print(res)
    return ["".join(x) for x in res]


class FrozenLakeEnv(Env):
    """
    Frozen lake involves crossing a frozen lake from Start(S) to Goal(G) without falling into any Holes(H) by walking over
    the Frozen(F) lake. The agent may not always move in the intended direction due to the slippery nature of the frozen lake.
    ### Action Space
    The agent takes a 1-element vector for actions.
    The action space is `(dir)`, where `dir` decides direction to move in which can be:
    - 0: LEFT
    - 1: DOWN
    - 2: RIGHT
    - 3: UP
    ### Observation Space
    The observation is a value representing the agent's current position as
    current_row * nrows + current_col (where both the row and col start at 0).
    For example, the goal position in the 4x4 map can be calculated as follows: 3 * 4 + 3 = 15.
    The number of possible observations is dependent on the size of the map.
    For example, the 4x4 map has 16 possible observations.
    ### Rewards
    Reward schedule:
    - Reach goal(G): +1
    - Reach hole(H): 0
    - Reach frozen(F): 0
    ### Arguments
    ```
    gym.make('FrozenLake-v1', desc=None,map_name="4x4", is_slippery=True)
    ```
    `desc`: Used to specify custom map for frozen lake. For example,
        desc=["SFFF", "FHFH", "FFFH", "HFFG"].
    `map_name`: ID to use any of the preloaded maps.
        "4x4":[
            "SFFF",
            "FHFH",
            "FFFH",
            "HFFG"
            ]
        "8x8": [
            "SFFFFFFF",
            "FFFFFFFF",
            "FFFHFFFF",
            "FFFFFHFF",
            "FFFHFFFF",
            "FHHFFFHF",
            "FHFFHFHF",
            "FFFHFFFG",
        ]
    `is_slippery`: True/False. If True will move in intended direction with
    probability of 1/3 else will move in either perpendicular direction with
    equal probability of 1/3 in both directions.
        For example, if action is left and is_slippery is True, then:
        - P(move left)=1/3
        - P(move up)=1/3
        - P(move down)=1/3
    ### Version History
    * v1: Bug fixes to rewards
    * v0: Initial versions release (1.0.0)
    """

    metadata = {"render_modes": ["human", "ansi", "rgb_array"], "render_fps": 4}

    def __init__(self, desc=None, map_name=None, is_slippery=True, map_size=8, frozen_p=0.8):
        if desc is None and map_name is None:
            desc = generate_random_map(size=map_size, p=frozen_p)
        elif desc is None:
            desc = MAPS[map_name]
        self.desc = desc = np.asarray(desc, dtype="c")
        self.nrow, self.ncol = nrow, ncol = desc.shape
        self.reward_range = (0, 1)

        # work out where the goal is, by first flattening map array 
        # then getting index of first (and only, hopefully) occurance of "G"
        self.goal = desc.ravel().tolist().index(b"G")

        nA = 4
        nS = nrow * ncol
        nO = 16 # 16, if holes are present on all 4 sides, 
                # but this scenario would never happen if the map generator does it job properly
        nDir = 4

        self.initial_state_distrib = np.array(desc == b"S").astype("float64").ravel()
        self.initial_state_distrib /= self.initial_state_distrib.sum()

        self.P = {s: {a: [] for a in range(nA)} for s in range(nS)}

        # returns integer representation of 2D coordinates as state
        def to_s(row, col):
            return row * ncol + col

        def inc(row, col, a):
            if a == LEFT:
                col = max(col - 1, 0)
            elif a == DOWN:
                row = min(row + 1, nrow - 1)
            elif a == RIGHT:
                col = min(col + 1, ncol - 1)
            elif a == UP:
                row = max(row - 1, 0)
            return (row, col)

        def update_probability_matrix(row, col, action):
            newrow, newcol = inc(row, col, action)
            newstate = to_s(newrow, newcol)
            newletter = desc[newrow, newcol]
            done = bytes(newletter) in b"GH"
            reward = float(newletter == b"G")
            # punishment for falling in hole
            if (newletter == b"H"): reward = -1
            return newstate, reward, done

        # probabilities of position transistion
        for row in range(nrow):
            for col in range(ncol):
                s = to_s(row, col)
                for a in range(4):
                    li = self.P[s][a]
                    letter = desc[row, col]
                    if letter in b"GH":
                        li.append((1.0, s, 0, True))
                    else:
                        if is_slippery:
                            for b in [(a - 1) % 4, a, (a + 1) % 4]:
                                li.append(
                                    (1.0 / 3.0, *update_probability_matrix(row, col, b))
                                )
                        else:
                            li.append((1.0, *update_probability_matrix(row, col, a)))

        # self.observation_space = spaces.Discrete(nS)
        # observation space modified to only contain adjacent tiles and goal direction features
        self.observation_space = spaces.Discrete(nO)
        # self.observation_space = spaces.Tuple((spaces.Discrete(nO), spaces.Discrete(nDir)))
        self.action_space = spaces.Discrete(nA)

        # pygame utils
        self.window_size = (min(64 * ncol, 512), min(64 * nrow, 512))
        self.window_surface = None
        self.clock = None
        self.hole_img = None
        self.cracked_hole_img = None
        self.ice_img = None
        self.elf_images = None
        self.goal_img = None
        self.start_img = None

    # new function to turn position state into 2D coordinates
    def _to_rc(self, s):
        col = s % self.ncol
        row = s // self.ncol
        return (row, col)

    def _is_hole(self, row, col):
        desc = self.desc
        return desc[row, col] == b"H"

    # hole checking algorithm
    def _check_adjacent(self):
        row, col = self._to_rc(self.s)
        desc = self.desc
        hole_state = 0b0000

        # left
        if not col-1 < 0:
            # print(0, desc[row,col-1])
            if self._is_hole(row,col-1):
                hole_state += 0b0001
        # down
        if not row+1 > self.nrow-1:
            # print(1, desc[row+1,col])
            if self._is_hole(row+1,col):
                hole_state += 0b0010
        # right
        if not col+1 > self.ncol-1:
            # print(2, desc[row,col+1])
            if self._is_hole(row,col+1):
                hole_state += 0b0100
        # up
        if not row-1 < 0:
            # print(3, desc[row-1,col])
            if self._is_hole(row-1,col):
                hole_state += 0b1000
        return int(hole_state)

    def _check_direction(self):
        rc_goal = self._to_rc(self.goal)
        rc_pos = self._to_rc(self.s)
        vector = np.array(rc_goal) - np.array(rc_pos)
        direction = 0

        dominant_axis = np.argmax(abs(vector))
        if dominant_axis == 0: 
            if vector[0] < 0: direction = 3
            elif vector[0] > 0: direction = 1
        elif dominant_axis == 1:
            if vector[1] < 0: direction = 0 
            elif vector[1] > 0: direction = 2
        return int(direction)

    def step(self, a):
        transitions = self.P[self.s][a]
        i = categorical_sample([t[0] for t in transitions], self.np_random)
        # print(transitions[i], self.s)
        p, s, r, d = transitions[i]
        self.s = s
        self.lastaction = a
        # return (int(s), r, d, {"prob": p})
        # return (self._check_adjacent(), self._check_direction()), r, d, {"prob": p}
        return self._check_adjacent(), r, d, {"prob": p}

    def reset(
        self,
        *,
        seed: Optional[int] = None,
        return_info: bool = False,
        options: Optional[dict] = None,
    ):
        super().reset(seed=seed)
        self.s = categorical_sample(self.initial_state_distrib, self.np_random)
        self.lastaction = None
        # print(self.desc)

        if not return_info:
            # return int(self.s)
            return (self._check_adjacent())
            # return (self._check_adjacent(), self._check_direction())
        else:
            # return int(self.s), {"prob": 1}
            return (self._check_adjacent()), {"prob": 1}
            # return (self._check_adjacent(), self._check_direction()), {"prob": 1}

    def render(self, mode="human"):
        desc = self.desc.tolist()
        if mode == "ansi":
            return self._render_text(desc)
        else:
            return self._render_gui(desc, mode)

    def _render_gui(self, desc, mode):
        if self.window_surface is None:
            pygame.init()
            pygame.display.init()
            pygame.display.set_caption("Frozen Lake")
            if mode == "human":
                self.window_surface = pygame.display.set_mode(self.window_size)
            else:  # rgb_array
                self.window_surface = pygame.Surface(self.window_size)
        if self.clock is None:
            self.clock = pygame.time.Clock()
        if self.hole_img is None:
            file_name = path.join(path.dirname(__file__), "img/hole.png")
            self.hole_img = pygame.image.load(file_name)
        if self.cracked_hole_img is None:
            file_name = path.join(path.dirname(__file__), "img/cracked_hole.png")
            self.cracked_hole_img = pygame.image.load(file_name)
        if self.ice_img is None:
            file_name = path.join(path.dirname(__file__), "img/ice.png")
            self.ice_img = pygame.image.load(file_name)
        if self.goal_img is None:
            file_name = path.join(path.dirname(__file__), "img/goal.png")
            self.goal_img = pygame.image.load(file_name)
        if self.start_img is None:
            file_name = path.join(path.dirname(__file__), "img/stool.png")
            self.start_img = pygame.image.load(file_name)
        if self.elf_images is None:
            elfs = [
                path.join(path.dirname(__file__), "img/elf_left.png"),
                path.join(path.dirname(__file__), "img/elf_down.png"),
                path.join(path.dirname(__file__), "img/elf_right.png"),
                path.join(path.dirname(__file__), "img/elf_up.png"),
            ]
            self.elf_images = [pygame.image.load(f_name) for f_name in elfs]

        board = pygame.Surface(self.window_size, flags=SRCALPHA)
        cell_width = self.window_size[0] // self.ncol
        cell_height = self.window_size[1] // self.nrow
        smaller_cell_scale = 0.6
        small_cell_w = smaller_cell_scale * cell_width
        small_cell_h = smaller_cell_scale * cell_height

        # prepare images
        last_action = self.lastaction if self.lastaction is not None else 1
        elf_img = self.elf_images[last_action]
        elf_scale = min(
            small_cell_w / elf_img.get_width(),
            small_cell_h / elf_img.get_height(),
        )
        elf_dims = (
            elf_img.get_width() * elf_scale,
            elf_img.get_height() * elf_scale,
        )
        elf_img = pygame.transform.scale(elf_img, elf_dims)
        hole_img = pygame.transform.scale(self.hole_img, (cell_width, cell_height))
        cracked_hole_img = pygame.transform.scale(
            self.cracked_hole_img, (cell_width, cell_height)
        )
        ice_img = pygame.transform.scale(self.ice_img, (cell_width, cell_height))
        goal_img = pygame.transform.scale(self.goal_img, (cell_width, cell_height))
        start_img = pygame.transform.scale(self.start_img, (small_cell_w, small_cell_h))

        for y in range(self.nrow):
            for x in range(self.ncol):
                rect = (x * cell_width, y * cell_height, cell_width, cell_height)
                if desc[y][x] == b"H":
                    self.window_surface.blit(hole_img, (rect[0], rect[1]))
                elif desc[y][x] == b"G":
                    self.window_surface.blit(ice_img, (rect[0], rect[1]))
                    goal_rect = self._center_small_rect(rect, goal_img.get_size())
                    self.window_surface.blit(goal_img, goal_rect)
                elif desc[y][x] == b"S":
                    self.window_surface.blit(ice_img, (rect[0], rect[1]))
                    stool_rect = self._center_small_rect(rect, start_img.get_size())
                    self.window_surface.blit(start_img, stool_rect)
                else:
                    self.window_surface.blit(ice_img, (rect[0], rect[1]))

                pygame.draw.rect(board, (180, 200, 230), rect, 1)

        # paint the elf
        bot_row, bot_col = self.s // self.ncol, self.s % self.ncol
        cell_rect = (
            bot_col * cell_width,
            bot_row * cell_height,
            cell_width,
            cell_height,
        )
        if desc[bot_row][bot_col] == b"H":
            self.window_surface.blit(cracked_hole_img, (cell_rect[0], cell_rect[1]))
        else:
            elf_rect = self._center_small_rect(cell_rect, elf_img.get_size())
            self.window_surface.blit(elf_img, elf_rect)

        self.window_surface.blit(board, board.get_rect())
        if mode == "human":
            pygame.event.pump()
            pygame.display.update()
            self.clock.tick(self.metadata["render_fps"])
        else:  # rgb_array
            return np.transpose(
                np.array(pygame.surfarray.pixels3d(self.window_surface)), axes=(1, 0, 2)
            )

    @staticmethod
    def _center_small_rect(big_rect, small_dims):
        offset_w = (big_rect[2] - small_dims[0]) / 2
        offset_h = (big_rect[3] - small_dims[1]) / 2
        return (
            big_rect[0] + offset_w,
            big_rect[1] + offset_h,
        )

    def _render_text(self, desc):
        outfile = StringIO()

        row, col = self.s // self.ncol, self.s % self.ncol
        desc = [[c.decode("utf-8") for c in line] for line in desc]
        desc[row][col] = utils.colorize(desc[row][col], "red", highlight=True)
        if self.lastaction is not None:
            outfile.write(f"  ({['Left', 'Down', 'Right', 'Up'][self.lastaction]})\n")
        else:
            outfile.write("\n")
        outfile.write("\n".join("".join(line) for line in desc) + "\n")

        with closing(outfile):
            return outfile.getvalue()

    def close(self):
        if self.window_surface is not None:
            pygame.display.quit()
            pygame.quit()


# Elf and stool from https://franuka.itch.io/rpg-snow-tileset
# All other assets by Mel Sawyer http://www.cyaneus.com/