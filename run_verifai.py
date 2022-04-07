
from dotmap import DotMap

from verifai.samplers.scenic_sampler import ScenicSampler
from verifai.scenic_server import ScenicServer
from verifai.falsifier import generic_falsifier
import os
from scenic.core.vectors import Vector
import math
from verifai.monitor import specification_monitor, mtl_specification
# from utils import sampleWithFeedback, checkSaveRestore


class MyMonitor(specification_monitor):
    '''
    This monitor class defines the specification in metric temporal logic to evaluate each simulation. 
    '''
    def __init__(self): # Do not modify this method!
        self.specification = mtl_specification(['G safe'])
        super().__init__(self.specification)

    def evaluate(self, simulation): 
        '''
        TODO: modify this method
        The following parsed traj (i.e. trajectory) information is in the following format where it is a list of tuples. 
        The list contains all objects' position and heading (in radians) at every simulation time step.
        The index of each tuple in the list corresponds to the simulation time step when the info in the tuple is collected. 
        Each tuple consists of a collection of tuples, where each tuple corresponds to an object. 

        More specifically, the parsed out trajectory is of the following form:
        traj = [((obj0_pos_x_0, obj0_pos_y_0, obj0_heading_0), (obj1_pos_x_0, obj1_pos_y_0, obj1_heading_0), ..., (objN_pos_x_0, objN_pos_y_0, obj2_heading_0)),...
         ((obj0_pos_x_t, obj0_pos_y_t, , obj0_heading_t), ..., (objN_pos_x_t, objN_pos_y_t, objN_heading_t))]
        where objk_pos_x_i, objk_pos_y_i, objk_heading_i are the kth object's (x,y) position and heading at ith simulation timestep. 
        '''

        traj = simulation.traj # do not modify this

        # Define the specification (TODO: modify this part)
        eval_dictionary = {'safe' : [[index, self.compute_dist(traj[index])-5] for index in range(len(traj))]}
        return self.specification.evaluate(eval_dictionary)

    def compute_dist(self, coords): # TODO: modify this method
        vector0 = coords[0]
        vector1 = coords[1]

        x0, y0 = vector0[0], vector0[1]
        x1, y1 = vector1[0], vector1[1]

        return math.sqrt(math.pow(x0-x1,2) +  math.pow(y0-y1,2))

## TODO: modify the path to your git cloned Scenic repo
path_dir = '/Users/edwardkim/Scenic'
path = os.path.join(path_dir, 'examples/driving/Tutorial/tutorial1.scenic')
sampler = ScenicSampler.fromScenario(path)


## TODO: modify the verifai configuration to fit your need
falsifier_params = DotMap(
    n_iters=3, # number of simulations to run
    save_error_table=True, # whether to save "safe" table which saves the environment info where specification is satisified
    save_safe_table=True, # whether to save "error" table which saves the environment info where specification is violated
    error_table_path='error_table.csv',
    safe_table_path='safe_table.csv'
)

# maxSteps = number of simulation timesteps per simulation (1 simulation timestep = 0.1 second)
server_options = DotMap(maxSteps=100, verbosity=0)  # no need to modify 'verbosity'

# ------------------------ do not modify below ------------------------------
falsifier = generic_falsifier(sampler=sampler,
                              monitor = MyMonitor(),
                              falsifier_params=falsifier_params,
                              server_class=ScenicServer,
                              server_options=server_options)
falsifier.run_falsifier()
print('end of test')
print("error_table: ", falsifier.error_table.table)
print("safe_table: ", falsifier.safe_table.table)