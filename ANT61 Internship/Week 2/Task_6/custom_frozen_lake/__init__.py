from gym.envs.registration import register
register(id="CustomFrozenLake", entry_point="custom_frozen_lake.envs:FrozenLakeEnv")