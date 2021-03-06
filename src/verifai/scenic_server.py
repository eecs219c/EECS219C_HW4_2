"""Specialized server for using Scenic's dynamic simulator interfaces."""

import time

from dotmap import DotMap

from verifai.server import Server
from verifai.samplers.scenic_sampler import ScenicSampler
from verifai.monitor import multi_objective_monitor
from scenic.core.simulators import SimulationCreationError
from scenic.core.external_params import VerifaiSampler
from scenic.core.distributions import RejectionException
import ray
from ray.util import ActorPool
from ray.util.multiprocessing import Pool
import progressbar

class ScenicServer(Server):
    def __init__(self, sampling_data, monitor, options={}):
        if sampling_data.sampler is None:
            raise RuntimeError('ScenicServer created without sampler')
        self.sampler = sampling_data.sampler
        if not isinstance(self.sampler, ScenicSampler):
            raise RuntimeError('only a ScenicSampler can be used with ScenicServer')
        self.sample_space = self.sampler.space
        self.simulator = self.sampler.scenario.getSimulator()
        extSampler = self.sampler.scenario.externalSampler
        if extSampler is None:
            self.rejectionFeedback = None
        else:
            self.rejectionFeedback = extSampler.rejectionFeedback
        self.monitor = monitor
        self.lastValue = None
        defaults = DotMap(maxSteps=None, verbosity=0, maxIterations=1)
        defaults.update(options)
        self.maxSteps = defaults.maxSteps
        self.verbosity = defaults.verbosity
        self.maxIterations = defaults.maxIterations

    def run_server(self):
        start = time.time()
        sample = self.sampler.nextSample(self.lastValue)
        scene = self.sampler.lastScene
        assert scene
        after_sampling = time.time()
        result = self._simulate(scene)
        if result is None:
            self.lastValue = self.rejectionFeedback
        else:
            self.lastValue = (0 if self.monitor is None
                              else self.monitor.evaluate(result))
        after_simulation = time.time()
        return sample, self.lastValue, (after_sampling - start, after_simulation - after_sampling)

    def _simulate(self, scene):
        startTime = time.time()
        if self.verbosity >= 1:
            print('  Beginning simulation...')
        try:
            result = self.simulator.simulate(scene,
                maxSteps=self.maxSteps, verbosity=self.verbosity,
                maxIterations=self.maxIterations)
        except SimulationCreationError as e:
            if self.verbosity >= 1:
                print(f'  Failed to create simulation: {e}')
            return None
        if self.verbosity >= 1:
            totalTime = time.time() - startTime
            print(f'  Ran simulation in {totalTime:.4g} seconds.')
        return result

    def terminate(self):
        pass

class DummySampler(VerifaiSampler):

    def nextSample(self, feedback):
        return self.last_sample

@ray.remote
class SampleSimulator():

    def __init__(self, scenic_path, worker_num, monitor, options={},
    scenario_params={}):
        print(scenario_params)
        scenario_params.update({
            'port': 2000 + 2*worker_num
        })
        self.sampler = ScenicSampler.fromScenario(scenic_path, maxIterations=1, **scenario_params)
        # reset self.sampler.scenario.externalSampler to dummy sampler
        # that reads argument
        self.worker_num = worker_num
        self.sampler.scenario.externalSampler = DummySampler(self.sampler.scenario.externalParams,
        self.sampler.scenario.params)
        self.simulator = self.sampler.scenario.getSimulator()
        self.monitor = monitor
        extSampler = self.sampler.scenario.externalSampler
        if extSampler is None:
            self.rejectionFeedback = None
        else:
            self.rejectionFeedback = extSampler.rejectionFeedback
        defaults = DotMap(maxSteps=None, verbosity=0, maxIterations=1)
        defaults.update(options)
        self.maxSteps = defaults.maxSteps
        self.verbosity = defaults.verbosity
        self.maxIterations = defaults.maxIterations

    def get_sample(self, sample):
        self.sampler.scenario.externalSampler.last_sample = sample
        self.full_sample = self.sampler.nextSample(sample)

    def simulate(self, sample):

        '''
        Need to generate scene from sample here.
        '''
        t0 = time.time()
        self.sampler.scenario.externalSampler.last_sample = sample
        scene = self.sampler.lastScene
        startTime = time.time()
        if self.verbosity >= 1:
            print('  Beginning simulation...')
        try:
            result = self.simulator.simulate(scene,
                maxSteps=self.maxSteps, verbosity=self.verbosity,
                maxIterations=self.maxIterations)
            result.worker_num = self.worker_num
        except SimulationCreationError as e:
            if self.verbosity >= 1:
                print(f'  Failed to create simulation: {e}')
            self.lastValue = self.rejectionFeedback
            return self.worker_num, self.full_sample, self.lastValue
        except RuntimeError as e:
            print(f'Runtime error during simulation: {e}')
            print('Waiting 1 minute before continuing...')
            time.sleep(60)
            return self.worker_num, self.full_sample, self.lastValue
        if self.verbosity >= 1:
            totalTime = time.time() - startTime
            print(f'  Ran simulation in {totalTime:.4g} seconds.')
        if result is None:
            self.lastValue = self.rejectionFeedback
        else:
            self.lastValue = (0 if self.monitor is None
                              else self.monitor.evaluate(result))
        return self.worker_num, self.full_sample, self.lastValue

class ParallelScenicServer(ScenicServer):

    def __init__(self, total_workers, n_iters, sampling_data, scenic_path, monitor,
    options={}, max_time=None, scenario_params={}, sampler=None):
        if not ray.is_initialized():
            ray.init(ignore_reinit_error=True)
        self.total_workers = total_workers
        self.n_iters = n_iters
        self.max_time = max_time
        # sampler = ScenicSampler.fromScenario(scenic_path, **scenario_params)
        sampling_data.sampler = sampler
        super().__init__(sampling_data, monitor, options)
        print(f'Sampler class is {type(self.sampler)}')
        self.sample_simulators = [SampleSimulator.remote(scenic_path, i, monitor, options,
        scenario_params)
        for i in range(self.total_workers)]

    def _generate_next_sample(self, worker_num):
        i = 0
        ext = self.sampler.scenario.externalSampler
        while i < 2000:
            ext.cachedSample, info = ext.getSample()
            sample = ext.cachedSample
            sim = self.sample_simulators[worker_num]
            try:
                ray.get(sim.get_sample.remote(sample))
                return sample, info
            except SimulationCreationError as e:
                if self.verbosity >= 1:
                    print(f'  Failed to create simulation: {e}')
                return None, None
            except RejectionException as e:
                i += 1
                continue
        return None, None

    def run_server(self):
        results = []
        futures = []
        samples = []
        infos = []
        if self.n_iters is not None:
            bar = progressbar.ProgressBar(max_value=self.n_iters)
        else:
            print(f'Creating widgets with max_time = {self.max_time}')
            widgets = ['Scenes generated: ', progressbar.Counter('%(value)d'),
               ' (', progressbar.Timer(), ')']
            bar = progressbar.ProgressBar(widgets=widgets)
        for i in range(self.total_workers):
            next_sample, info = self._generate_next_sample(i)
            samples.append(next_sample)
            infos.append(info)
            sim = self.sample_simulators[i]
            futures.append(sim.simulate.remote(next_sample))
        while True:
            done, _ = ray.wait(futures)
            result = ray.get(done[0])
            index, sample, rho = result
            self.lastValue = rho
            results.append((sample, rho))
            info = infos[index]
            self.sampler.scenario.externalSampler.update(sample, info, rho)
            bar.update(len(results))
            if len(results) == 1:
                t0 = time.time()
            elapsed = time.time() - t0
            if self.n_iters is not None and len(results) >= self.n_iters:
                break
            if self.max_time is not None and elapsed >= self.max_time:
                break
            next_sample, info = self._generate_next_sample(index)
            elapsed = time.time() - t0
            sim = self.sample_simulators[index]
            samples[index] = next_sample
            infos[index] = info
            futures[index] = sim.simulate.remote(next_sample)

        return results
