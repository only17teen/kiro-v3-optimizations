use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use parking_lot::RwLock;
use serde::{Deserialize, Serialize};

/// Actor state optimized for FFI
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ActorState {
    pub id: String,
    pub status: ActorStatus,
    pub message_count: u64,
    pub error_count: u64,
    pub last_activity: u64,
    pub metadata: HashMap<String, String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum ActorStatus {
    Running,
    Paused,
    Stopped,
    Failed,
}

/// Thread-safe actor registry using sharded locks
pub struct ActorRegistry {
    shards: Vec<RwLock<<HashMap<String, Arc<<ActorState>>>>,
    shard_mask: usize,
    total_actors: AtomicU64,
}

impl ActorRegistry {
    pub fn new(shard_count: usize) -> Self {
        let shards: Vec<_> = (0..shard_count)
            .map(|_| RwLock::new(HashMap::new()))
            .collect();
        
        ActorRegistry {
            shards,
            shard_mask: shard_count - 1,
            total_actors: AtomicU64::new(0),
        }
    }
    
    fn get_shard(&self, id: &str) -> usize {
        // FNV-1a hash for fast distribution
        let mut hash: u64 = 0xcbf29ce484222325;
        for byte in id.bytes() {
            hash ^= byte as u64;
            hash = hash.wrapping_mul(0x100000001b3);
        }
        (hash as usize) & self.shard_mask
    }
    
    pub fn register(&self, state: ActorState) -> bool {
        let shard_idx = self.get_shard(&state.id);
        let mut shard = self.shards[shard_idx].write();
        
        if shard.contains_key(&state.id) {
            return false;
        }
        
        shard.insert(state.id.clone(), Arc::new(state));
        self.total_actors.fetch_add(1, Ordering::Relaxed);
        true
    }
    
    pub fn get(&self, id: &str) -> Option<<Arc<<ActorState>> {
        let shard_idx = self.get_shard(id);
        let shard = self.shards[shard_idx].read();
        shard.get(id).cloned()
    }
    
    pub fn update_status(&self, id: &str, status: ActorStatus) -> bool {
        let shard_idx = self.get_shard(id);
        let mut shard = self.shards[shard_idx].write();
        
        if let Some(state) = shard.get_mut(id) {
            let new_state = ActorState {
                status,
                ..(**state).clone()
            };
            *state = Arc::new(new_state);
            true
        } else {
            false
        }
    }
    
    pub fn remove(&self, id: &str) -> bool {
        let shard_idx = self.get_shard(id);
        let mut shard = self.shards[shard_idx].write();
        
        if shard.remove(id).is_some() {
            self.total_actors.fetch_sub(1, Ordering::Relaxed);
            true
        } else {
            false
        }
    }
    
    pub fn count(&self) -> u64 {
        self.total_actors.load(Ordering::Relaxed)
    }
    
    pub fn all_states(&self) -> Vec<<Arc<<ActorState>> {
        let mut result = Vec::new();
        for shard in &self.shards {
            let shard_data = shard.read();
            result.extend(shard_data.values().cloned());
        }
        result
    }
}

/// FFI exports for Python integration
use std::ffi::{CStr, CString};
use std::os::raw::{c_char, c_int};

static mut REGISTRY: Option<<ActorRegistry> = None;

#[no_mangle]
pub extern "C" fn kiro_init_registry(shard_count: c_int) -> c_int {
    unsafe {
        REGISTRY = Some(ActorRegistry::new(shard_count as usize));
    }
    0
}

#[no_mangle]
pub extern "C" fn kiro_register_actor(id: *const c_char, status: c_int) -> c_int {
    unsafe {
        let id_str = match CStr::from_ptr(id).to_str() {
            Ok(s) => s.to_string(),
            Err(_) => return -1,
        };
        
        let status = match status {
            0 => ActorStatus::Running,
            1 => ActorStatus::Paused,
            2 => ActorStatus::Stopped,
            3 => ActorStatus::Failed,
            _ => return -1,
        };
        
        if let Some(registry) = &REGISTRY {
            let state = ActorState {
                id: id_str,
                status,
                message_count: 0,
                error_count: 0,
                last_activity: 0,
                metadata: HashMap::new(),
            };
            if registry.register(state) {
                0
            } else {
                -2
            }
        } else {
            -3
        }
    }
}

#[no_mangle]
pub extern "C" fn kiro_actor_count() -> c_int {
    unsafe {
        if let Some(registry) = &REGISTRY {
            registry.count() as c_int
        } else {
            -1
        }
    }
}

#[no_mangle]
pub extern "C" fn kiro_cleanup() {
    unsafe {
        REGISTRY = None;
    }
}