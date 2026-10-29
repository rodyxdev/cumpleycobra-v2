//! Escrow de Cumple&Cobra v2.
//!
//! Una instancia del contrato custodia pagos en un token (USDC en testnet) para muchas tareas.
//! El árbitro (el backend de verificación) y el token se fijan en el constructor.
//!
//! Ciclo de vida de una tarea:
//!   deposit (cliente) → Funded → accept (freelancer) → Accepted
//!     → release (árbitro, con workspace_hash + verdict_hash del veredicto aprobado) → Released
//!     → client_release (cliente, p. ej. tras una revisión manual)                  → Released
//!   refund (cualquiera, pasado el plazo, si no hubo release)                         → Refunded
//!
//! El contrato no puede saber si una verificación aprobó: eso lo decide el árbitro off-chain y lo
//! deja registrado on-chain con los hashes del workspace y del veredicto.

#![no_std]

use soroban_sdk::{contract, contracterror, contractevent, contractimpl, contracttype, token, Address, BytesN, Env};

/// ~30 días de ledgers (5 s por ledger).
const TTL_EXTEND_TO: u32 = 518_400;
const TTL_THRESHOLD: u32 = TTL_EXTEND_TO / 2;

#[contracttype]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(u32)]
pub enum Status {
    Funded = 0,
    Accepted = 1,
    Released = 2,
    Refunded = 3,
}

#[contracttype]
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Escrow {
    pub client: Address,
    pub freelancer: Option<Address>,
    pub amount: i128,
    /// Plazo como timestamp UNIX (segundos) del ledger.
    pub deadline: u64,
    pub rules_hash: BytesN<32>,
    pub status: Status,
    pub workspace_hash: Option<BytesN<32>>,
    pub verdict_hash: Option<BytesN<32>>,
}

#[contracttype]
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Config {
    pub arbiter: Address,
    pub token: Address,
}

#[contracttype]
enum DataKey {
    Config,
    Escrow(BytesN<32>),
}

#[contracterror]
#[derive(Copy, Clone, Debug, Eq, PartialEq, PartialOrd, Ord)]
#[repr(u32)]
pub enum Error {
    AlreadyExists = 1,
    NotFound = 2,
    InvalidAmount = 3,
    InvalidDeadline = 4,
    WrongStatus = 5,
    DeadlineNotReached = 6,
    DeadlinePassed = 7,
    ClientCannotAccept = 8,
}

#[contractevent]
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Deposited {
    #[topic]
    pub task_id: BytesN<32>,
    pub client: Address,
    pub amount: i128,
    pub deadline: u64,
    pub rules_hash: BytesN<32>,
}

#[contractevent]
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Accepted {
    #[topic]
    pub task_id: BytesN<32>,
    pub freelancer: Address,
}

#[contractevent]
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Released {
    #[topic]
    pub task_id: BytesN<32>,
    pub freelancer: Address,
    pub amount: i128,
    pub by_arbiter: bool,
    pub workspace_hash: Option<BytesN<32>>,
    pub verdict_hash: Option<BytesN<32>>,
}

#[contractevent]
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Refunded {
    #[topic]
    pub task_id: BytesN<32>,
    pub client: Address,
    pub amount: i128,
}

#[contract]
pub struct EscrowContract;

fn config(env: &Env) -> Config {
    env.storage().instance().get(&DataKey::Config).unwrap()
}

fn load(env: &Env, task_id: &BytesN<32>) -> Result<Escrow, Error> {
    env.storage().persistent().get(&DataKey::Escrow(task_id.clone())).ok_or(Error::NotFound)
}

fn store(env: &Env, task_id: &BytesN<32>, escrow: &Escrow) {
    let key = DataKey::Escrow(task_id.clone());
    env.storage().persistent().set(&key, escrow);
    env.storage().persistent().extend_ttl(&key, TTL_THRESHOLD, TTL_EXTEND_TO);
    env.storage().instance().extend_ttl(TTL_THRESHOLD, TTL_EXTEND_TO);
}

fn pay(env: &Env, to: &Address, amount: i128) {
    token::Client::new(env, &config(env).token).transfer(&env.current_contract_address(), to, &amount);
}

#[contractimpl]
impl EscrowContract {
    pub fn __constructor(env: Env, arbiter: Address, token: Address) {
        env.storage().instance().set(&DataKey::Config, &Config { arbiter, token });
    }

    /// El cliente deposita `amount` del token para la tarea `task_id`, con plazo y reglas congeladas.
    pub fn deposit(
        env: Env,
        client: Address,
        task_id: BytesN<32>,
        amount: i128,
        deadline: u64,
        rules_hash: BytesN<32>,
    ) -> Result<(), Error> {
        client.require_auth();
        if amount <= 0 {
            return Err(Error::InvalidAmount);
        }
        if deadline <= env.ledger().timestamp() {
            return Err(Error::InvalidDeadline);
        }
        if env.storage().persistent().has(&DataKey::Escrow(task_id.clone())) {
            return Err(Error::AlreadyExists);
        }
        token::Client::new(&env, &config(&env).token).transfer(&client, &env.current_contract_address(), &amount);
        let escrow = Escrow {
            client: client.clone(),
            freelancer: None,
            amount,
            deadline,
            rules_hash: rules_hash.clone(),
            status: Status::Funded,
            workspace_hash: None,
            verdict_hash: None,
        };
        store(&env, &task_id, &escrow);
        Deposited { task_id, client, amount, deadline, rules_hash }.publish(&env);
        Ok(())
    }

    /// El freelancer acepta la tarea: el pago queda amarrado a su dirección.
    pub fn accept(env: Env, freelancer: Address, task_id: BytesN<32>) -> Result<(), Error> {
        freelancer.require_auth();
        let mut escrow = load(&env, &task_id)?;
        if escrow.status != Status::Funded {
            return Err(Error::WrongStatus);
        }
        if env.ledger().timestamp() > escrow.deadline {
            return Err(Error::DeadlinePassed);
        }
        if freelancer == escrow.client {
            return Err(Error::ClientCannotAccept);
        }
        escrow.freelancer = Some(freelancer.clone());
        escrow.status = Status::Accepted;
        store(&env, &task_id, &escrow);
        Accepted { task_id, freelancer }.publish(&env);
        Ok(())
    }

    /// Solo el árbitro: libera el pago al freelancer tras un veredicto aprobado y deja los hashes.
    pub fn release(
        env: Env,
        task_id: BytesN<32>,
        workspace_hash: BytesN<32>,
        verdict_hash: BytesN<32>,
    ) -> Result<(), Error> {
        config(&env).arbiter.require_auth();
        let mut escrow = load(&env, &task_id)?;
        if escrow.status != Status::Accepted {
            return Err(Error::WrongStatus);
        }
        let freelancer = escrow.freelancer.clone().unwrap();
        pay(&env, &freelancer, escrow.amount);
        escrow.status = Status::Released;
        escrow.workspace_hash = Some(workspace_hash.clone());
        escrow.verdict_hash = Some(verdict_hash.clone());
        store(&env, &task_id, &escrow);
        Released {
            task_id,
            freelancer,
            amount: escrow.amount,
            by_arbiter: true,
            workspace_hash: Some(workspace_hash),
            verdict_hash: Some(verdict_hash),
        }
        .publish(&env);
        Ok(())
    }

    /// Solo el cliente: libera manualmente (p. ej. cuando el veredicto quedó en revisión manual).
    pub fn client_release(env: Env, task_id: BytesN<32>) -> Result<(), Error> {
        let mut escrow = load(&env, &task_id)?;
        escrow.client.require_auth();
        if escrow.status != Status::Accepted {
            return Err(Error::WrongStatus);
        }
        let freelancer = escrow.freelancer.clone().unwrap();
        pay(&env, &freelancer, escrow.amount);
        escrow.status = Status::Released;
        store(&env, &task_id, &escrow);
        Released { task_id, freelancer, amount: escrow.amount, by_arbiter: false, workspace_hash: None, verdict_hash: None }
            .publish(&env);
        Ok(())
    }

    /// Cualquiera, pasado el plazo y sin release: devuelve el depósito al cliente.
    pub fn refund(env: Env, task_id: BytesN<32>) -> Result<(), Error> {
        let mut escrow = load(&env, &task_id)?;
        if escrow.status != Status::Funded && escrow.status != Status::Accepted {
            return Err(Error::WrongStatus);
        }
        if env.ledger().timestamp() <= escrow.deadline {
            return Err(Error::DeadlineNotReached);
        }
        pay(&env, &escrow.client, escrow.amount);
        escrow.status = Status::Refunded;
        store(&env, &task_id, &escrow);
        Refunded { task_id, client: escrow.client, amount: escrow.amount }.publish(&env);
        Ok(())
    }

    pub fn get_escrow(env: Env, task_id: BytesN<32>) -> Result<Escrow, Error> {
        load(&env, &task_id)
    }

    pub fn get_config(env: Env) -> Config {
        config(&env)
    }
}

mod test;
