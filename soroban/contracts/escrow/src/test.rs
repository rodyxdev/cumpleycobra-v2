#![cfg(test)]

use super::*;
use soroban_sdk::testutils::{Address as _, Ledger, MockAuth, MockAuthInvoke};
use soroban_sdk::{token::StellarAssetClient, token::TokenClient, IntoVal};

const NOW: u64 = 1_800_000_000;
const DAY: u64 = 86_400;

struct Setup<'a> {
    env: Env,
    contract: EscrowContractClient<'a>,
    token: TokenClient<'a>,
    arbiter: Address,
    client: Address,
    freelancer: Address,
}

fn setup() -> Setup<'static> {
    let env = Env::default();
    env.ledger().with_mut(|l| l.timestamp = NOW);
    let arbiter = Address::generate(&env);
    let client = Address::generate(&env);
    let freelancer = Address::generate(&env);
    let issuer = Address::generate(&env);
    let sac = env.register_stellar_asset_contract_v2(issuer);
    env.mock_all_auths();
    StellarAssetClient::new(&env, &sac.address()).mint(&client, &1_000_0000000);
    let id = env.register(EscrowContract, (arbiter.clone(), sac.address()));
    let contract = EscrowContractClient::new(&env, &id);
    let token = TokenClient::new(&env, &sac.address());
    Setup { env, contract, token, arbiter, client, freelancer }
}

fn hash(env: &Env, byte: u8) -> BytesN<32> {
    BytesN::from_array(env, &[byte; 32])
}

fn funded(s: &Setup) -> BytesN<32> {
    let task = hash(&s.env, 1);
    s.contract.deposit(&s.client, &task, &100_0000000, &(NOW + 7 * DAY), &hash(&s.env, 9));
    task
}

fn accepted(s: &Setup) -> BytesN<32> {
    let task = funded(s);
    s.contract.accept(&s.freelancer, &task);
    task
}

#[test]
fn deposit_moves_funds_into_escrow() {
    let s = setup();
    let task = funded(&s);
    assert_eq!(s.token.balance(&s.client), 900_0000000);
    assert_eq!(s.token.balance(&s.contract.address), 100_0000000);
    let e = s.contract.get_escrow(&task);
    assert_eq!(e.status, Status::Funded);
    assert_eq!(e.client, s.client);
    assert_eq!(e.rules_hash, hash(&s.env, 9));
    assert_eq!(e.freelancer, None);
}

#[test]
fn deposit_requires_client_auth() {
    let s = setup();
    let task = hash(&s.env, 1);
    s.contract.deposit(&s.client, &task, &5, &(NOW + DAY), &hash(&s.env, 9));
    let auths = s.env.auths();
    assert_eq!(auths[0].0, s.client);
}

#[test]
fn deposit_validations() {
    let s = setup();
    let task = hash(&s.env, 1);
    let rules = hash(&s.env, 9);
    assert_eq!(s.contract.try_deposit(&s.client, &task, &0, &(NOW + DAY), &rules), Err(Ok(Error::InvalidAmount)));
    assert_eq!(s.contract.try_deposit(&s.client, &task, &5, &NOW, &rules), Err(Ok(Error::InvalidDeadline)));
    s.contract.deposit(&s.client, &task, &5, &(NOW + DAY), &rules);
    assert_eq!(s.contract.try_deposit(&s.client, &task, &5, &(NOW + DAY), &rules), Err(Ok(Error::AlreadyExists)));
}

#[test]
fn accept_binds_freelancer() {
    let s = setup();
    let task = accepted(&s);
    let e = s.contract.get_escrow(&task);
    assert_eq!(e.status, Status::Accepted);
    assert_eq!(e.freelancer, Some(s.freelancer.clone()));
    // Un segundo freelancer no puede quitarle la tarea.
    let other = Address::generate(&s.env);
    assert_eq!(s.contract.try_accept(&other, &task), Err(Ok(Error::WrongStatus)));
}

#[test]
fn client_cannot_accept_own_task_and_late_accept_fails() {
    let s = setup();
    let task = funded(&s);
    assert_eq!(s.contract.try_accept(&s.client, &task), Err(Ok(Error::ClientCannotAccept)));
    s.env.ledger().with_mut(|l| l.timestamp = NOW + 8 * DAY);
    assert_eq!(s.contract.try_accept(&s.freelancer, &task), Err(Ok(Error::DeadlinePassed)));
}

#[test]
fn arbiter_release_pays_freelancer_and_records_hashes() {
    let s = setup();
    let task = accepted(&s);
    s.contract.release(&task, &hash(&s.env, 2), &hash(&s.env, 3));
    assert_eq!(s.env.auths()[0].0, s.arbiter);
    assert_eq!(s.token.balance(&s.freelancer), 100_0000000);
    assert_eq!(s.token.balance(&s.contract.address), 0);
    let e = s.contract.get_escrow(&task);
    assert_eq!(e.status, Status::Released);
    assert_eq!(e.workspace_hash, Some(hash(&s.env, 2)));
    assert_eq!(e.verdict_hash, Some(hash(&s.env, 3)));
    // No se puede pagar dos veces ni reembolsar después.
    assert_eq!(s.contract.try_release(&task, &hash(&s.env, 2), &hash(&s.env, 3)), Err(Ok(Error::WrongStatus)));
    s.env.ledger().with_mut(|l| l.timestamp = NOW + 30 * DAY);
    assert_eq!(s.contract.try_refund(&task), Err(Ok(Error::WrongStatus)));
}

#[test]
fn release_by_anyone_but_the_arbiter_is_rejected() {
    let s = setup();
    let task = accepted(&s);
    let ws = hash(&s.env, 2);
    let verdict = hash(&s.env, 3);
    // Solo se simula la firma del freelancer: la del árbitro falta y la llamada debe fallar.
    s.env.set_auths(&[]);
    let result = s
        .contract
        .mock_auths(&[MockAuth {
            address: &s.freelancer,
            invoke: &MockAuthInvoke {
                contract: &s.contract.address,
                fn_name: "release",
                args: (task.clone(), ws.clone(), verdict.clone()).into_val(&s.env),
                sub_invokes: &[],
            },
        }])
        .try_release(&task, &ws, &verdict);
    assert!(result.is_err());
    assert_eq!(s.token.balance(&s.freelancer), 0);
}

#[test]
fn release_requires_accepted_status() {
    let s = setup();
    let task = funded(&s);
    assert_eq!(s.contract.try_release(&task, &hash(&s.env, 2), &hash(&s.env, 3)), Err(Ok(Error::WrongStatus)));
    let missing = hash(&s.env, 7);
    assert_eq!(s.contract.try_release(&missing, &hash(&s.env, 2), &hash(&s.env, 3)), Err(Ok(Error::NotFound)));
}

#[test]
fn client_release_pays_freelancer() {
    let s = setup();
    let task = accepted(&s);
    s.contract.client_release(&task);
    assert_eq!(s.env.auths()[0].0, s.client);
    assert_eq!(s.token.balance(&s.freelancer), 100_0000000);
    let e = s.contract.get_escrow(&task);
    assert_eq!(e.status, Status::Released);
    assert_eq!(e.verdict_hash, None);
}

#[test]
fn client_release_needs_client_signature() {
    let s = setup();
    let task = accepted(&s);
    s.env.set_auths(&[]);
    let result = s
        .contract
        .mock_auths(&[MockAuth {
            address: &s.freelancer,
            invoke: &MockAuthInvoke {
                contract: &s.contract.address,
                fn_name: "client_release",
                args: (task.clone(),).into_val(&s.env),
                sub_invokes: &[],
            },
        }])
        .try_client_release(&task);
    assert!(result.is_err());
}

#[test]
fn refund_only_after_deadline_and_by_anyone() {
    let s = setup();
    let task = accepted(&s);
    assert_eq!(s.contract.try_refund(&task), Err(Ok(Error::DeadlineNotReached)));
    s.env.ledger().with_mut(|l| l.timestamp = NOW + 7 * DAY + 1);
    s.env.set_auths(&[]); // sin firmas: cualquiera puede disparar el reembolso
    s.contract.refund(&task);
    assert_eq!(s.token.balance(&s.client), 1_000_0000000);
    assert_eq!(s.contract.get_escrow(&task).status, Status::Refunded);
    assert_eq!(s.contract.try_refund(&task), Err(Ok(Error::WrongStatus)));
}

#[test]
fn refund_of_unaccepted_task_after_deadline() {
    let s = setup();
    let task = funded(&s);
    s.env.ledger().with_mut(|l| l.timestamp = NOW + 8 * DAY);
    s.contract.refund(&task);
    assert_eq!(s.token.balance(&s.client), 1_000_0000000);
}

#[test]
fn tasks_are_independent() {
    let s = setup();
    let a = hash(&s.env, 1);
    let b = hash(&s.env, 2);
    s.contract.deposit(&s.client, &a, &10, &(NOW + DAY), &hash(&s.env, 9));
    s.contract.deposit(&s.client, &b, &20, &(NOW + DAY), &hash(&s.env, 9));
    s.contract.accept(&s.freelancer, &b);
    s.contract.release(&b, &hash(&s.env, 4), &hash(&s.env, 5));
    assert_eq!(s.contract.get_escrow(&a).status, Status::Funded);
    assert_eq!(s.token.balance(&s.contract.address), 10);
    assert_eq!(s.contract.get_config().arbiter, s.arbiter);
}
