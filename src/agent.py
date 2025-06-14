from abc import abstractmethod
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
import numpy as np
from typing import Tuple
from network import QNetwork, SmallQNetwork , VNetwork

class Agent():
    def __init__(
        self,
        memory_size: int,
        state_dimensions: Tuple[int, int, int],
        n_actions: int,
    ) -> None:
        """!
        Initializes the agent.
        Agent is an abstract class that should be inherited by any agent that
        wants to interact with the environment. The agent should be able to
        store transitions, choose actions based on observations, and learn from the
        transitions.

        @param memory_size (int): Size of the memory buffer
        @param state_dimensions (int): Number of dimensions of the state space
        @param n_actions (int): Number of actions the agent can take
        """

        self.memory_size = memory_size
        self.state_buffer = np.zeros((self.memory_size, *state_dimensions), dtype=np.float32)
        self.next_state_buffer = np.zeros((self.memory_size, *state_dimensions), dtype=np.float32)
        self.action_buffer = np.zeros(self.memory_size, dtype=np.int32)
        self.reward_buffer = np.zeros(self.memory_size, dtype=np.float32)
        self.terminal_buffer = np.zeros(self.memory_size, dtype=bool)  
        self.mem_counter = 0
        self.n_actions = n_actions


    def store_transition(
        self,
        state: np.ndarray,
        action: int,  
        reward: float,
        new_state: np.ndarray,
        done: bool
    ) -> None:
        
        index = self.mem_counter % self.memory_size # modulus to overwrite old transitions
        self.state_buffer[index] = state
        self.action_buffer[index] = action
        self.reward_buffer[index] = reward
        self.next_state_buffer[index] = new_state
        self.terminal_buffer[index] = done
        self.mem_counter += 1
        """!
        Stores the state transition for later memory replay.
        Make sure that the memory buffer does not exceed its maximum size.

        Hint: after reaching the limit of the memory buffer, maybe you should start overwriting
        the oldest transitions?

        @param state        (list): Vector describing current state
        @param action       (int): Action taken
        @param reward       (float): Received reward
        @param new_state    (list): Newly observed state.
        """


    @abstractmethod
    def choose_action(
        self,
        observation: np.ndarray
    ) -> int: 
        """!
        Abstract method that should be implemented by the child class, e.g. DQN or DDQN agents.
        This method should contain the full logic needed to choose an action based on the current state.
        Maybe you can store the neural network in the agent class and use it here to decide which action to take?

        @param observation (np.ndarray): Vector describing current state

        @return (int): Action to take
        """

        return 0

    @abstractmethod
    def learn(self) -> None:
        """!
        Update the parameters of the internal networks.
        This method should be implemented by the child class.
        """

        pass
class DQNAgent(Agent):
    def __init__(
        self,
        memory_size: int,
        state_dimensions: Tuple[int, int, int], # (height, width, fps)
        n_actions: int,
        q_network_class,        
        **kwargs
         ):
        super(DQNAgent, self).__init__(memory_size, state_dimensions,  n_actions)
        self.lr = kwargs.get("learning_rate", 0.0001)
        self.gamma = kwargs.get("gamma", 0.99)
        self.epsilon = kwargs.get("epsilon_start", 1.0)
        self.epsilon_min = kwargs.get("epsilon_min", 0.0001)
        self.epsilon_decay = kwargs.get("epsilon_decay", 0.986)
        self.batch_size = kwargs.get("batch_size", 32)
        self.target_update_frequency = kwargs.get("target_update_frequency", 1000)
        self.burn_in_period = kwargs.get("burn_in_period", 7000)

        self.learn_step_counter = 0 # For target network updates

        # Input_dims for QNetwork is (84, 84, FPS) 
        self.q_network = q_network_class(input_dims=state_dimensions, n_actions=n_actions)
        self.q_target_network = q_network_class(input_dims=state_dimensions, n_actions=n_actions)  
        self.q_target_network.load_state_dict(self.q_network.state_dict()) # Initialize target with eval weights with parameter tensor state_dict
        self.q_target_network.eval() # Put target network in eval mode

        self.optimizer = optim.Adam(self.q_network.parameters(), lr=self.lr)
        self.loss_fn = nn.MSELoss()

        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.q_network.to(self.device)
        self.q_target_network.to(self.device)   

    def choose_action(self, observation: np.ndarray) -> int:
        ''' choose action, 
        input state (84,84,4) from environment 
        output action
        
        '''


        # with chance epsilon we explore
        if np.random.rand() <= self.epsilon:
            return np.random.choice(self.n_actions)
        
        # else we exploit
        else:
            # Convert observation to PyTorch tensor and add batch dimension in first dim with unsqueeze(0)
            # Neural net excpects input of shape (batch_size, channels, height, width)
            state = torch.tensor(observation, dtype=torch.float32).unsqueeze(0).to(self.device)

            self.q_network.eval() # Set q network to evaluation mode
            with torch.no_grad(): 
                q_values = self.q_network(state) #compute q values for current state
            self.q_network.train() # Set network back to train mode

            return torch.argmax(q_values).item() 
        

    def learn(self) -> None:
        if self.mem_counter < self.burn_in_period: # Not enough samples yet
            return  

        self.optimizer.zero_grad() 

        # Sample a mini-batch from memory, consider case memory is not full yet
        max_mem = min(self.mem_counter, self.memory_size)

        # pick batch_size number of random indices without replacement
        batch_indices = np.random.choice(max_mem, self.batch_size, replace=False)  

        states_batch = torch.tensor(self.state_buffer[batch_indices], dtype=torch.float32).to(self.device)
        actions_batch = torch.tensor(self.action_buffer[batch_indices], dtype=torch.long).to(self.device) # .long() for indexing
        rewards_batch = torch.tensor(self.reward_buffer[batch_indices], dtype=torch.float32).to(self.device)
        new_states_batch = torch.tensor(self.next_state_buffer[batch_indices], dtype=torch.float32).to(self.device)
        terminal_batch = torch.tensor(self.terminal_buffer[batch_indices], dtype=torch.bool).to(self.device)

        q_s = self.q_network(states_batch) # Shape: (batch_size, n_actions)

        # Gather Q-values corresponding to the actions taken
        q_action_taken = q_s.gather(1, actions_batch.unsqueeze(1)).squeeze(1) 


        # Get Q-values for next states from q_target_network
        q_next = self.q_target_network(new_states_batch) # Shape: (batch_size, n_actions)
        q_next[terminal_batch] = 0.0 # Q-value of terminal state is 0

        # Calculate target Q-values
        #  For DQN, target is R + gamma * max_a'(Q_target(s', a'))
        q_target = rewards_batch + self.gamma * torch.max(q_next, dim=1)[0] # [0] to get values from (values, indices) tuple

        # Calculate loss
        loss = self.loss_fn(q_action_taken, q_target)

        # Backpropagate
        loss.backward()
        self.optimizer.step()

        self.learn_step_counter += 1

        # If we reached the target update frequency, update the target network
        if self.learn_step_counter % self.target_update_frequency == 0:
            self.q_target_network.load_state_dict(self.q_network.state_dict())

        # Epsilon decay
        self.epsilon = self.epsilon * self.epsilon_decay if self.epsilon > self.epsilon_min else self.epsilon_min
class DDQNAgent(Agent):
    def __init__(
        self,
        memory_size: int,
        state_dimensions: Tuple[int, int, int], 
        n_actions: int,
        q_network_class,
        t_weight_start: float = 0.2,
        soft_update: bool = False,  #whether to use soft update or hard update
        **kwargs 
        ):
        super(DDQNAgent, self).__init__(memory_size, state_dimensions,  n_actions)
        self.lr = kwargs.get("learning_rate", 0.0001)
        self.gamma = kwargs.get("gamma", 0.99)
        self.epsilon = kwargs.get("epsilon_start", 1.0)
        self.epsilon_min = kwargs.get("epsilon_min", 0.0001)
        self.epsilon_decay = kwargs.get("epsilon_decay", 0.986)
        self.batch_size = kwargs.get("batch_size", 32)
        self.target_update_frequency = kwargs.get("target_update_frequency", 1000)
        self.burn_in_period = kwargs.get("burn_in_period", 7000)

        self.learn_step_counter = 0 # For target network updates
        self.t_weight = t_weight_start
        self.soft_update = soft_update


        self.q_network = q_network_class(input_dims=state_dimensions, n_actions=n_actions)
        self.q_target_network = q_network_class(input_dims=state_dimensions, n_actions=n_actions) 
        self.q_target_network.eval() # Put target network in eval mode

        self.optimizer = optim.Adam(self.q_network.parameters(), lr=self.lr)
        self.loss_fn = nn.MSELoss()

        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.q_network.to(self.device)
        self.q_target_network.to(self.device)   


    def choose_action(self, observation: np.ndarray) -> int:
        ''' choose action, 
        input state (84,84,4) from environment 
        output action
        
        '''


        # with chance epsilon we explore
        if np.random.rand() <= self.epsilon:
            return np.random.choice(self.n_actions)
        
        # else we exploit
        else:
            # Convert observation to PyTorch tensor and add batch dimension in first dim with unsqueeze(0)
            # Neural net excpects input of shape (batch_size, channels, height, width)
            state = torch.tensor(observation, dtype=torch.float32).unsqueeze(0).to(self.device)

            self.q_network.eval() # Set q network to evaluation mode, is it necessary?
            with torch.no_grad(): 
                q_values = self.q_network(state) #comput q values for current state
            self.q_network.train() # Set network back to train mode

            return torch.argmax(q_values).item() 
        

    def learn(self) -> None:
        if self.mem_counter < self.burn_in_period: # Not enough samples yet
            return  

        self.optimizer.zero_grad() # Reset gradients before backpropagation

        # Sample a mini-batch from memory, consider case memory is not full yet
        max_mem = min(self.mem_counter, self.memory_size)

        # pick batch_size number of random indices without replacement
        batch_indices = np.random.choice(max_mem, self.batch_size, replace=False)  

        states_batch = torch.tensor(self.state_buffer[batch_indices], dtype=torch.float32).to(self.device)
        actions_batch = torch.tensor(self.action_buffer[batch_indices], dtype=torch.long).to(self.device) # .long() for indexing
        rewards_batch = torch.tensor(self.reward_buffer[batch_indices], dtype=torch.float32).to(self.device)
        new_states_batch = torch.tensor(self.next_state_buffer[batch_indices], dtype=torch.float32).to(self.device)
        terminal_batch = torch.tensor(self.terminal_buffer[batch_indices], dtype=torch.bool).to(self.device)

        q_s = self.q_network(states_batch) # Shape: (batch_size, n_actions)
        # Gather Q-values corresponding to the actions taken
        q_action_taken = q_s.gather(1, actions_batch.unsqueeze(1)).squeeze(1) 


        q_snext= self.q_network(new_states_batch) # next state q values
        q_snext[terminal_batch] = 0.0 # Q-value of terminal state is 0
        q_actions_batch= torch.argmax(q_snext,dim=1) #argmax actions in next state

        with torch.no_grad():
            target_q_snext= self.q_target_network(new_states_batch) # shape (batch_size, n_actions)
            target_q_snext[terminal_batch] = 0.0 
            # action target for double dqn
            q_action_target= target_q_snext.gather(1, q_actions_batch.unsqueeze(1)).squeeze(1) 

        target = rewards_batch + self.gamma*q_action_target # [0] to get values from (values, indices) tuple

        # Calculate loss
        loss = self.loss_fn(q_action_taken, target)

        # Backpropagate
        loss.backward()
        self.optimizer.step()
        self.learn_step_counter += 1

        # If we reached the target update frequency, update the target network
        if self.learn_step_counter % self.target_update_frequency == 0:
            # soft update
            if self.soft_update == True:
                for target_param, param in zip(self.q_target_network.parameters(), self.q_network.parameters()):
                    target_param.data.copy_(self.t_weight * param.data + (1 - self.t_weight) * target_param.data)
            # hard update
            else:
                self.q_target_network.load_state_dict(self.q_network.state_dict())
            # Epsilon decay
        self.epsilon = max(self.epsilon * self.epsilon_decay,self.epsilon_min)

class DQVAgent(Agent): 
    def __init__(
        self,
        memory_size: int,
        state_dimensions: Tuple[int, int, int],
        n_actions: int,
        q_network_class, 
        v_network_class, 
        **kwargs
    ):
        super(DQVAgent, self).__init__(memory_size, state_dimensions, n_actions)
        self.lr = kwargs.get("learning_rate", 0.00025)
        self.gamma = kwargs.get("gamma", 0.99)
        self.epsilon = kwargs.get("epsilon_start", 1.0) # Paper uses 0.5 for DQV/DQV-Max
        self.epsilon_min = kwargs.get("epsilon_min", 0.001)
        self.epsilon_decay = kwargs.get("epsilon_decay", 0.9998) 
        self.batch_size = kwargs.get("batch_size", 32)
        self.target_update_frequency = kwargs.get("target_update_frequency", 2000) 
        self.burn_in_period = kwargs.get("burn_in_period", 7000) # As per paper's N_cal

        self.learn_step_counter = 0

        self.q_network = q_network_class(input_dims=state_dimensions, n_actions=n_actions)
        self.v_network = v_network_class(input_dims=state_dimensions)
        self.target_v_network = v_network_class(input_dims=state_dimensions) 

        self.target_v_network.load_state_dict(self.v_network.state_dict())
        self.target_v_network.eval()

        self.q_optimizer = optim.Adam(self.q_network.parameters(), lr=self.lr)
        self.v_optimizer = optim.Adam(self.v_network.parameters(), lr=self.lr)
        self.loss_fn = nn.MSELoss()

        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.q_network.to(self.device)
        self.v_network.to(self.device)
        self.target_v_network.to(self.device)

    def choose_action(self, observation: np.ndarray) -> int:
        if np.random.rand() <= self.epsilon:
            return np.random.choice(self.n_actions)
        else:
            state = torch.tensor(observation, dtype=torch.float32).unsqueeze(0).to(self.device)
            self.q_network.eval()
            with torch.no_grad():
                q_values = self.q_network(state)
            self.q_network.train()
            return torch.argmax(q_values).item()

    def learn(self) -> None:
        if self.mem_counter < self.burn_in_period:
            return

        self.q_optimizer.zero_grad()
        self.v_optimizer.zero_grad()

        max_mem = min(self.mem_counter, self.memory_size)
        batch_indices = np.random.choice(max_mem, self.batch_size, replace=False)

        states_batch = torch.tensor(self.state_buffer[batch_indices], dtype=torch.float32).to(self.device)
        actions_batch = torch.tensor(self.action_buffer[batch_indices], dtype=torch.long).to(self.device)
        rewards_batch = torch.tensor(self.reward_buffer[batch_indices], dtype=torch.float32).to(self.device)
        new_states_batch = torch.tensor(self.next_state_buffer[batch_indices], dtype=torch.float32).to(self.device)
        terminal_batch = torch.tensor(self.terminal_buffer[batch_indices], dtype=torch.bool).to(self.device)

        # Calculate TD target for both V and Q networks 
        # target^DQV = r_t + gamma * V(s_{t+1}; Phi^-)
        with torch.no_grad():
            v_s_next_target = self.target_v_network(new_states_batch).squeeze()
            v_s_next_target[terminal_batch] = 0.0
            target_dqv = rewards_batch + self.gamma * v_s_next_target

        # --- Update V-Network ---
        v_s = self.v_network(states_batch).squeeze()
        v_loss = self.loss_fn(target_dqv, v_s)
        v_loss.backward()
        self.v_optimizer.step()

        # --- Update Q-Network ---
        q_s= self.q_network(states_batch)
        q_action_taken = q_s.gather(1, actions_batch.unsqueeze(-1)).squeeze(-1)
        q_loss = self.loss_fn(target_dqv, q_action_taken)
        q_loss.backward()
        self.q_optimizer.step()

        self.learn_step_counter += 1

        if self.learn_step_counter % self.target_update_frequency == 0:
            self.target_v_network.load_state_dict(self.v_network.state_dict()) 

        self.epsilon = max(self.epsilon_min, self.epsilon * self.epsilon_decay)

class DQVMaxAgent(Agent):
    def __init__(
        self,
        memory_size: int,
        state_dimensions: Tuple[int, int, int],
        n_actions: int,
        q_network_class, 
        v_network_class, 
        **kwargs
    ):
        super(DQVMaxAgent, self).__init__(memory_size, state_dimensions, n_actions)
        self.lr = kwargs.get("learning_rate", 0.00025) 
        self.gamma = kwargs.get("gamma", 0.99)
        self.epsilon = kwargs.get("epsilon_start", 0.5) 
        self.epsilon_min = kwargs.get("epsilon_min", 0.001) 
        self.epsilon_decay = kwargs.get("epsilon_decay", 0.9998)
        self.batch_size = kwargs.get("batch_size", 32)
        self.target_update_frequency = kwargs.get("target_update_frequency", 200) 
        self.burn_in_period = kwargs.get("burn_in_period", 7000) 

        self.learn_step_counter = 0

        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        # Initialize Q-Network and Target Q-Network
        self.q_network = q_network_class(input_dims=state_dimensions, n_actions=n_actions).to(self.device)
        self.target_q_network = q_network_class(input_dims=state_dimensions, n_actions=n_actions).to(self.device)
        self.target_q_network.load_state_dict(self.q_network.state_dict())
        self.target_q_network.eval() # Target network is only for inference

        # Initialize V-Network
        self.v_network = v_network_class(input_dims=state_dimensions).to(self.device)


        self.q_optimizer = optim.Adam(self.q_network.parameters(), lr=self.lr)
        self.v_optimizer = optim.Adam(self.v_network.parameters(), lr=self.lr)
        self.loss_fn = nn.MSELoss()


    def choose_action(self, observation: np.ndarray) -> int:
        if np.random.rand() <= self.epsilon:
            return np.random.choice(self.n_actions)
        else:
            state = torch.tensor(observation, dtype=torch.float32).unsqueeze(0).to(self.device)
            self.q_network.eval() 
            with torch.no_grad():
                q_values = self.q_network(state)
            self.q_network.train() 
            return torch.argmax(q_values).item()

    def learn(self) -> None:
        if self.mem_counter < self.burn_in_period: 
            return

        self.q_optimizer.zero_grad()
        self.v_optimizer.zero_grad()

        max_mem = min(self.mem_counter, self.memory_size)
        batch_indices = np.random.choice(max_mem, self.batch_size, replace=False)

        states_batch = torch.tensor(self.state_buffer[batch_indices], dtype=torch.float32).to(self.device)
        actions_batch = torch.tensor(self.action_buffer[batch_indices], dtype=torch.long).to(self.device)
        rewards_batch = torch.tensor(self.reward_buffer[batch_indices], dtype=torch.float32).to(self.device)
        new_states_batch = torch.tensor(self.next_state_buffer[batch_indices], dtype=torch.float32).to(self.device)
        terminal_batch = torch.tensor(self.terminal_buffer[batch_indices], dtype=torch.bool).to(self.device)

        # --- Update V-Network ---
        # Target for V-network = r_t + gamma * max_a' Q(s_{t+1}, a'; theta^-)
        with torch.no_grad():
            q_next_target = self.target_q_network(new_states_batch) 
            max_q_next_target = torch.max(q_next_target, dim=1)[0]
            max_q_next_target[terminal_batch] = 0.0 
            v_target = rewards_batch + self.gamma * max_q_next_target
        
        v_s = self.v_network(states_batch).squeeze() 
        v_loss = self.loss_fn(v_target, v_s)
        v_loss.backward() 
        self.v_optimizer.step() 

        # --- Update Q-Network ---
        # Target for Q-network = r_t + gamma * V(s_{t+1}; Phi)
        with torch.no_grad():
            v_s_next = self.v_network(new_states_batch).squeeze() 
            v_s_next[terminal_batch] = 0.0 
            q_target = rewards_batch + self.gamma * v_s_next
        
        q_s = self.q_network(states_batch) 
        q_action_taken = q_s.gather(1, actions_batch.unsqueeze(-1)).squeeze(-1) 
        q_loss = self.loss_fn(q_target, q_action_taken)
        q_loss.backward() 
        self.q_optimizer.step() 

        self.learn_step_counter += 1

        if self.learn_step_counter % self.target_update_frequency == 0:
            self.target_q_network.load_state_dict(self.q_network.state_dict())

        self.epsilon = max(self.epsilon_min, self.epsilon * self.epsilon_decay)
