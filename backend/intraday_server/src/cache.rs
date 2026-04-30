use redis::AsyncCommands;

pub type RedisConn = redis::aio::ConnectionManager;

pub async fn get_bytes(conn: &mut RedisConn, key: &str) -> Option<Vec<u8>> {
    conn.get::<_, Option<Vec<u8>>>(key).await.ok().flatten()
}

pub async fn set_bytes_ex(conn: &mut RedisConn, key: &str, value: &[u8], ttl_secs: u64) {
    let _: redis::RedisResult<()> = conn.set_ex(key, value, ttl_secs).await;
}

pub async fn get_str(conn: &mut RedisConn, key: &str) -> Option<String> {
    conn.get::<_, Option<String>>(key).await.ok().flatten()
}

pub async fn set_str_ex(conn: &mut RedisConn, key: &str, value: &str, ttl_secs: u64) {
    let _: redis::RedisResult<()> = conn.set_ex(key, value, ttl_secs).await;
}

/// SET key value EX ttl_secs NX (set only if not exists). Returns true if key was set.
pub async fn setnx_ex(conn: &mut RedisConn, key: &str, value: &str, ttl_secs: u64) -> bool {
    let result: redis::RedisResult<Option<String>> = redis::cmd("SET")
        .arg(key).arg(value)
        .arg("EX").arg(ttl_secs)
        .arg("NX")
        .query_async(conn)
        .await;
    result.ok().flatten().is_some()
}

pub async fn ping(conn: &mut RedisConn) -> bool {
    let result: redis::RedisResult<String> = redis::cmd("PING").query_async(conn).await;
    result.map(|s| s == "PONG").unwrap_or(false)
}

pub async fn del(conn: &mut RedisConn, key: &str) {
    let _: redis::RedisResult<()> = conn.del(key).await;
}
