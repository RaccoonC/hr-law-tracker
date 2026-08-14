// worker.js
//
// 這支程式是整個網站的入口，邏輯很單純：
//   1. 如果請求路徑是 /api/supplements，就處理「補充資料」的新增、查詢、刪除，
//      資料存在 Cloudflare KV（SUPPLEMENTS 這個綁定）。
//   2. 除此之外的所有請求（也就是 index.html、data/ 底下的 JSON 檔等等），
//      原封不動交給 Cloudflare 的靜態檔案服務處理，跟原本網站行為完全一樣。
//
// 補充資料目前全部存在同一把 KV key（"list"）底下，內容是一個 JSON 陣列。
// 這個做法在資料量不大（例如幾百筆以內）時最簡單、最不容易出錯；
// 如果之後補充資料筆數變得非常多（上千筆以上），可以考慮改成
// 每筆資料各自存一個 key（用 KV 的 list() 列舉），但目前用途不需要到那麼複雜。

const SUPPLEMENTS_KEY = "list";
const MAX_NAME_LENGTH = 200;
const MAX_CATEGORY_LENGTH = 50;
const MAX_CONTENT_LENGTH = 5000;

function jsonResponse(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

async function readSupplements(env) {
  const raw = await env.SUPPLEMENTS.get(SUPPLEMENTS_KEY);
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch (e) {
    // 資料萬一壞掉，寧可回傳空陣列讓網站正常運作，也不要讓整個 API 掛掉。
    console.error("補充資料 KV 內容解析失敗", e);
    return [];
  }
}

async function writeSupplements(env, list) {
  await env.SUPPLEMENTS.put(SUPPLEMENTS_KEY, JSON.stringify(list));
}

async function handleGet(env) {
  const items = await readSupplements(env);
  return jsonResponse({ items });
}

async function handlePost(request, env) {
  let body;
  try {
    body = await request.json();
  } catch (e) {
    return jsonResponse({ error: "請求內容不是合法的 JSON" }, 400);
  }

  const name = String(body.name || "").trim().slice(0, MAX_NAME_LENGTH);
  const category = String(body.category || "未分類").trim().slice(0, MAX_CATEGORY_LENGTH) || "未分類";
  const content = String(body.content || "").trim().slice(0, MAX_CONTENT_LENGTH);

  if (!name || !content) {
    return jsonResponse({ error: "資料名稱與內容為必填" }, 400);
  }

  const items = await readSupplements(env);
  const newItem = {
    id: crypto.randomUUID(),
    name,
    category,
    content,
    uploaded_at: new Date().toISOString(),
  };
  items.unshift(newItem); // 新的排在最前面，方便使用者一打開就看到最新新增的
  await writeSupplements(env, items);

  return jsonResponse({ item: newItem }, 201);
}

async function handleDelete(request, env) {
  const url = new URL(request.url);
  const id = url.searchParams.get("id");
  if (!id) {
    return jsonResponse({ error: "缺少要刪除的 id" }, 400);
  }

  const items = await readSupplements(env);
  const filtered = items.filter((item) => item.id !== id);
  if (filtered.length === items.length) {
    return jsonResponse({ error: "找不到該筆資料，可能已經被刪除過了" }, 404);
  }
  await writeSupplements(env, filtered);

  return jsonResponse({ deleted: id });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/api/supplements") {
      if (request.method === "GET") return handleGet(env);
      if (request.method === "POST") return handlePost(request, env);
      if (request.method === "DELETE") return handleDelete(request, env);
      return jsonResponse({ error: "不支援的方法" }, 405);
    }

    // 不是 /api/supplements 的請求，就照舊回傳靜態檔案
    return env.ASSETS.fetch(request);
  },
};
