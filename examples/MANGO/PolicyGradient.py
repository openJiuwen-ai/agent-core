import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
import networkx as nx
from sklearn.metrics.pairwise import cosine_similarity
from sentence_transformers import SentenceTransformer

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

class RL_Environment:
    def __init__(self, G: nx.DiGraph, node_to_rd_vecs, node_to_vecs):
        self.G = G
        # self.current_node = None
        # self.current_node_neighbours = None
        # self.step_num = 0
        # self.tvec = None
        # self.graph_path = None
        self.node_to_rd_vecs = node_to_rd_vecs
        self.node_to_vecs = node_to_vecs
        self.model_embedding = SentenceTransformer("./hugging_face/all-MiniLM-L6-v2")

    # def direct_step(self, init_node):
    #     self.current_node = init_node
    #     self.current_node_neighbours = list(self.G.successors(self.current_node))
    #     self.step_num += 1

    def step(self, action, step_num, current_node_neighbours, graph_path):
        
        current_node = current_node_neighbours[action]
        current_node_neighbours = list(self.G.successors(current_node))
        reward = 1.0 if step_num < len(graph_path) and current_node == graph_path[step_num] else 0.0
        done = current_node == 1 or step_num >= 8
        info = {}
        
        return reward, done, info, current_node, current_node_neighbours
    
    def step_k(self, action, step_num, current_node_neighbours, current_node_neighbours_step, graph_path, k):
        current_node = current_node_neighbours[action]
        skip_steps = current_node_neighbours_step[current_node]
        # current_node_neighbours = list(self.G.successors(current_node))
        # current_node_neighbours_step = {}
        # for current_node_neighbour in current_node_neighbours:
        #     current_node_neighbours_step[current_node_neighbour] = 1
        
        # neighbours = current_node_neighbours[:]
        # for i in range(2, k + 1):
        #     new_neighbours = []
        #     for neighbour in neighbours:
        #         new_neighbours += list(self.G.successors(neighbour))
        #     neighbours = new_neighbours[:]
        #     for neighbour in new_neighbours:
        #         current_node_neighbours_step[neighbour] = i
        #     current_node_neighbours += new_neighbours
        current_node_neighbours, current_node_neighbours_step = self.get_neighbours(current_node, k)
        
        reward = 1.0 if step_num < len(graph_path) and current_node == graph_path[step_num] else 0.0
        done = current_node == 1 or step_num >= 8
        info = {}
        
        return reward, done, info, current_node, current_node_neighbours, current_node_neighbours_step, skip_steps
    
    def get_neighbours(self, current_node, k):
        current_node_neighbours = list(self.G.successors(current_node))
        current_node_neighbours_step = {}
        for current_node_neighbour in current_node_neighbours:
            current_node_neighbours_step[current_node_neighbour] = 1
        
        neighbours = current_node_neighbours[:]
        for i in range(2, k + 1):
            new_neighbours = []
            for neighbour in neighbours:
                new_neighbours += list(self.G.successors(neighbour))
            neighbours = new_neighbours[:]
            for neighbour in new_neighbours:
                current_node_neighbours_step[neighbour] = i
            current_node_neighbours += new_neighbours
        return current_node_neighbours, current_node_neighbours_step
    
    def get_ob(self, neighbours, running_prompt, tvec):
        if neighbours == []:
            return None
        
        vec_RP = self.model_embedding.encode(running_prompt)
        vec_Q = tvec
        vec_RDs = []
        vec_CTs = []

        for neighbour in neighbours:
            vec_RDs.append(self.G.nodes[neighbour]['updated_system_prompt_vector'])
            # vec_RDs.append(self.node_to_rd_vecs[neighbour])
            # vec_CTs.append(self.G.nodes[neighbour]['content_vector'])
            vec_CTs.append(self.node_to_vecs[neighbour])

        RP_RD_Sim = cosine_similarity([vec_RP], vec_RDs)[0]
        Q_RD_Sim = cosine_similarity([vec_Q], vec_RDs)[0]
        RP_CT_Sim = cosine_similarity([vec_RP], vec_CTs)[0]
        Q_CT_Sim = cosine_similarity([vec_Q], vec_CTs)[0]
        return np.array([RP_RD_Sim, Q_RD_Sim, RP_CT_Sim, Q_CT_Sim]).T
    
    # def reset(self, tvec, graph_path):
    #     # self.current_node = init_node
    #     self.current_node = 0
    #     self.current_node_neighbours = list(self.G.successors(0))
    #     self.step_num = 0
    #     self.tvec = tvec
    #     self.graph_path = graph_path
    #     # neighbours = self.G.successors(init_node)

class PolicyNet(nn.Module):
    def __init__(self, n_features, n_hiddens):
        super(PolicyNet, self).__init__()
        self.fc1 = nn.Linear(n_features, n_hiddens)
        self.fc2 = nn.Linear(n_hiddens, 1)
    
    def forward(self, x):

        x = self.fc1(x)
        x = F.relu(x)
        x = self.fc2(x)

        return x.squeeze(-1)

class PolicyGradient:
    def __init__(self, n_features, n_hiddens, learning_rate = 0.001, gamma = 0.98):

        self.n_features = n_features # obs_num
        self.n_hiddens = n_hiddens
        self.learning_rate = learning_rate
        self.gamma = gamma  # discount factor
        self.ep_obs, self.ep_as, self.ep_rs = [], [], []
        self.ep_as_size = []
        self._build_net() # build net model

    def _build_net(self):
        self.policy_net = PolicyNet(self.n_features, self.n_hiddens).to(device)
        self.optimizer = torch.optim.Adam(self.policy_net.parameters(), lr=self.learning_rate)
        self.loss_fn = nn.CrossEntropyLoss(reduction='none').to(device)

    # Choose Action
    def choose_action(self, observation, greedy = False):
        if len(observation) == 1: 
            # print(f"Just 1 road, choose action 1")
            return 0
        observation = torch.FloatTensor(observation).to(device)
        out = self.policy_net(observation)
        # prob_weights = F.softmax(out)
        prob_weights = F.softmax(out, dim=0)
        prob_weights = prob_weights.cpu().detach().numpy()

        if greedy:
            action = np.argmax(prob_weights, axis=0)
        else:
            action = np.random.choice(range(prob_weights.shape[0]), p=prob_weights)
        # print(f"Total {len(prob_weights)} roads, choose action {action+1}")
        return action

    # 获取每个状态最大的state_value
    # def max_q_value(self, state):
    #     # 维度变换[n_states]-->[1,n_states]
    #     state = torch.tensor(state, dtype=torch.float).view(1,-1)
    #     # 获取状态对应的每个动作的reward的最大值 [1,n_states]-->[1,n_actions]-->[1]-->float
    #     max_q = self.policy_net(state).max().item()
    #     return max_q

    def store_transition(self, s, a, r):
        # if len(s) <= 1: return
        self.ep_obs.append(s)
        self.ep_as.append(a)
        self.ep_rs.append(r)
        self.ep_as_size.append(len(s))

    # 训练模型
    def learn(self):
        discounted_ep_rs_norm = self._discount_rewards()

        observations = pad_matrices_to_same_shape(self.ep_obs)
        observations = torch.FloatTensor(observations).to(device)
        
        actions = torch.LongTensor(np.stack(self.ep_as)).to(device)

        discounted_ep_rs_norm = torch.FloatTensor(discounted_ep_rs_norm).to(device)

        out = self.policy_net(observations)

        # To reduce probabilities of the nonexist actions to 0, set these logits to -1e9(e^-1e9 ≈ 0)
        for i in range(len(out)):
            out[i][self.ep_as_size[i]:] = -1e9

        neg_log_prob = self.loss_fn(out, actions)
        loss = torch.mean(neg_log_prob * discounted_ep_rs_norm)

        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()

        self.ep_obs, self.ep_as, self.ep_rs = [], [], []
        self.ep_as_size = []
        return discounted_ep_rs_norm

    def _discount_rewards(self):
        # discount episode rewards
        discounted_ep_rs = np.zeros_like(self.ep_rs)
        running_add = 0
        for t in reversed(range(len(self.ep_rs))):
            running_add = running_add * self.gamma + self.ep_rs[t]
            discounted_ep_rs[t] = running_add
        # normalize episode rewards
        discounted_ep_rs -= np.mean(discounted_ep_rs)
        discounted_ep_rs /= (np.std(discounted_ep_rs) + 0.000001)
        return discounted_ep_rs
    
    def save(self, checkpoint):
        torch.save(self.policy_net.state_dict(), checkpoint)

    def load(self, checkpoint):
        state = torch.load(checkpoint, map_location=device)
        self.policy_net.load_state_dict(state)

# 补零
def pad_matrices_to_same_shape(matrix_list, pad_value=0):

    if not matrix_list:
        return []
    
    ndim = matrix_list[0].ndim
    max_shape = [max(m.shape[i] for m in matrix_list) for i in range(ndim)]
    
    padded_matrices = []
    for matrix in matrix_list:
        pad_width = []
        for dim in range(ndim):
            pad_before = 0
            pad_after = max_shape[dim] - matrix.shape[dim]
            pad_width.append((pad_before, pad_after))
        
        padded = np.pad(matrix, pad_width, mode='constant', constant_values=pad_value)
        padded_matrices.append(padded)
    
    return np.array(padded_matrices)