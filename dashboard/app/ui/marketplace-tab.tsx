"use client";

import { useMemo, useRef, useState } from "react";

import { downloadCsv } from "./csv";
import { exportNodeAsPng } from "./export-utils";
import type { Offer, Purchase, Review, RunState } from "./types";

function parseTime(value: string | number | null | undefined) {
  if (typeof value === "number") {
    return value;
  }
  if (typeof value === "string" && value) {
    const parsed = Date.parse(value);
    return Number.isNaN(parsed) ? null : parsed;
  }
  return null;
}

function formatTime(value: string | number | null | undefined) {
  const timestamp = parseTime(value);
  if (timestamp === null) {
    return "—";
  }
  return new Date(timestamp).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function average(values: number[]) {
  if (!values.length) {
    return 0;
  }
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function topCounter(counter: Record<string, number>) {
  return Object.entries(counter).sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))[0]?.[0] ?? null;
}

function relationLabel(
  sellerAgentId: string,
  buyerAgentId: string,
  agentModels: Record<string, string | null>,
) {
  const sellerModel = agentModels[sellerAgentId];
  const buyerModel = agentModels[buyerAgentId];
  if (!sellerModel || !buyerModel) {
    return "unknown";
  }
  return sellerModel === buyerModel ? "same-model" : "cross-model";
}

export function MarketplaceTab({
  state,
  agentModels,
}: {
  state: RunState;
  agentModels: Record<string, string | null>;
}) {
  const flowRef = useRef<HTMLElement | null>(null);
  const offers = useMemo(
    () =>
      Object.values(state.offers ?? {}).sort((left, right) => {
        const lhs = parseTime(left.updated_at ?? left.created_at) ?? 0;
        const rhs = parseTime(right.updated_at ?? right.created_at) ?? 0;
        return rhs - lhs;
      }),
    [state.offers],
  );
  const purchases = useMemo(
    () =>
      Object.values(state.purchases ?? {}).sort((left, right) => {
        const lhs = parseTime(left.created_at) ?? 0;
        const rhs = parseTime(right.created_at) ?? 0;
        return rhs - lhs;
      }),
    [state.purchases],
  );
  const reviews = useMemo(
    () =>
      Object.values(state.reviews ?? {}).sort((left, right) => {
        const lhs = parseTime(left.created_at) ?? 0;
        const rhs = parseTime(right.created_at) ?? 0;
        return rhs - lhs;
      }),
    [state.reviews],
  );
  const bots = state.bots ?? {};

  const summary = useMemo(() => {
    const offerCounts: Record<string, number> = {};
    const purchaseCounts: Record<string, number> = {};
    let comparablePurchases = 0;
    let sameModelPurchases = 0;

    for (const offer of offers) {
      offerCounts[offer.seller_agent_id] = (offerCounts[offer.seller_agent_id] ?? 0) + 1;
    }

    for (const purchase of purchases) {
      purchaseCounts[purchase.buyer_agent_id] = (purchaseCounts[purchase.buyer_agent_id] ?? 0) + 1;
      const relation = relationLabel(purchase.seller_agent_id ?? "", purchase.buyer_agent_id, agentModels);
      if (relation === "same-model") {
        sameModelPurchases += 1;
      }
      if (relation !== "unknown") {
        comparablePurchases += 1;
      }
    }

    return {
      totalOffers: offers.length,
      totalPurchases: purchases.length,
      totalReviews: reviews.length,
      avgPricePct: average(offers.map((offer) => Number(offer.price_pct ?? 0))),
      mostActiveSeller: topCounter(offerCounts),
      mostActiveBuyer: topCounter(purchaseCounts),
      sameModelPurchasePct: comparablePurchases > 0 ? (sameModelPurchases / comparablePurchases) * 100 : null,
    };
  }, [agentModels, offers, purchases, reviews]);

  const flow = useMemo(() => {
    const agents = Array.from(
      new Set([
        ...Object.keys(agentModels),
        ...offers.map((offer) => offer.seller_agent_id),
        ...purchases.flatMap((purchase) => [purchase.seller_agent_id ?? "", purchase.buyer_agent_id]),
      ]),
    )
      .filter(Boolean)
      .sort((left, right) => left.localeCompare(right));
    const counts = new Map<string, number>();
    for (const purchase of purchases) {
      const key = `${purchase.seller_agent_id ?? ""}::${purchase.buyer_agent_id}`;
      counts.set(key, Number(counts.get(key) ?? 0) + 1);
    }
    return { agents, counts };
  }, [agentModels, offers, purchases]);

  const handleExportFlowPng = async () => {
    if (!flowRef.current) {
      return;
    }
    await exportNodeAsPng(flowRef.current, "marketplace-flow.png", "Marketplace Flow Matrix");
  };

  const handleExportOffersCsv = () => {
    downloadCsv(
      "marketplace-offers.csv",
      offers.map((offer) => ({
        offer_id: offer.offer_id,
        seller_agent_id: offer.seller_agent_id,
        bot_id: offer.bot_id ?? null,
        title: offer.title,
        description: offer.description,
        price_pct: offer.price_pct,
        status: offer.status ?? null,
        created_at: offer.created_at ?? null,
        updated_at: offer.updated_at ?? null,
      })),
    );
  };

  const handleExportPurchasesCsv = () => {
    downloadCsv(
      "marketplace-purchases.csv",
      purchases.map((purchase) => ({
        purchase_id: purchase.purchase_id,
        offer_id: purchase.offer_id,
        seller_agent_id: purchase.seller_agent_id ?? null,
        buyer_agent_id: purchase.buyer_agent_id,
        price_pct: purchase.price_pct ?? null,
        relation: relationLabel(purchase.seller_agent_id ?? "", purchase.buyer_agent_id, agentModels),
        created_at: purchase.created_at ?? null,
      })),
    );
  };

  const handleExportReviewsCsv = () => {
    downloadCsv(
      "marketplace-reviews.csv",
      reviews.map((review) => ({
        review_id: review.review_id,
        offer_id: review.offer_id,
        buyer_agent_id: review.buyer_agent_id,
        text: review.text,
        created_at: review.created_at ?? null,
      })),
    );
  };

  if (!offers.length) {
    return <div className="empty">No marketplace offers yet.</div>;
  }

  return (
    <div className="marketplace">
      <section className="marketplace-summary">
        <div className="marketplace-summary__card panel">
          <span className="marketplace-summary__label">Offers</span>
          <strong>{summary.totalOffers}</strong>
          <span>{summary.totalPurchases} purchases</span>
        </div>
        <div className="marketplace-summary__card panel">
          <span className="marketplace-summary__label">Reviews</span>
          <strong>{summary.totalReviews}</strong>
          <span>Avg price {summary.avgPricePct.toFixed(1)}%</span>
        </div>
        <div className="marketplace-summary__card panel">
          <span className="marketplace-summary__label">Most active seller</span>
          <strong>{summary.mostActiveSeller ?? "—"}</strong>
          <span>Most active buyer {summary.mostActiveBuyer ?? "—"}</span>
        </div>
        <div className="marketplace-summary__card panel">
          <span className="marketplace-summary__label">Same-model purchases</span>
          <strong>{summary.sameModelPurchasePct === null ? "—" : `${summary.sameModelPurchasePct.toFixed(0)}%`}</strong>
          <span>Cross-model highlights in the flow matrix</span>
        </div>
      </section>

      <section className="marketplace-tools panel">
        <div>
          <h3>Research Exports</h3>
          <p>Export the current marketplace surface using the data already loaded in the dashboard.</p>
        </div>
        <div className="marketplace-tools__actions" data-export-ignore="true">
          <button type="button" className="btn btn--sm" onClick={() => void handleExportFlowPng()}>
            Export Flow PNG
          </button>
          <button type="button" className="btn btn--sm" onClick={handleExportOffersCsv}>
            Offers CSV
          </button>
          <button type="button" className="btn btn--sm" onClick={handleExportPurchasesCsv}>
            Purchases CSV
          </button>
          <button type="button" className="btn btn--sm" onClick={handleExportReviewsCsv}>
            Reviews CSV
          </button>
        </div>
      </section>

      {flow.agents.length > 0 && (
        <section className="marketplace-flow panel" ref={flowRef}>
          <div className="marketplace-flow__header">
            <div>
              <h3>Marketplace Flow</h3>
              <p>Seller → buyer purchase counts, with same-model and cross-model cells called out.</p>
            </div>
          </div>
          <div className="marketplace-flow__table-wrap">
            <table className="data-table marketplace-flow__table">
              <thead>
                <tr>
                  <th>Seller \ Buyer</th>
                  {flow.agents.map((buyerAgentId) => (
                    <th key={buyerAgentId}>{buyerAgentId}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {flow.agents.map((sellerAgentId) => (
                  <tr key={sellerAgentId}>
                    <td>{sellerAgentId}</td>
                    {flow.agents.map((buyerAgentId) => {
                      const count = flow.counts.get(`${sellerAgentId}::${buyerAgentId}`) ?? 0;
                      const relation = relationLabel(sellerAgentId, buyerAgentId, agentModels);
                      return (
                        <td
                          key={`${sellerAgentId}-${buyerAgentId}`}
                          className="marketplace-flow__cell"
                          data-relation={relation}
                        >
                          {count > 0 ? count : "—"}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <div className="marketplace__offers">
        {offers.map((offer) => {
          const offerPurchases = purchases.filter((purchase) => purchase.offer_id === offer.offer_id);
          const offerReviews = reviews.filter((review) => review.offer_id === offer.offer_id);
          const bot = offer.bot_id
            ? (bots as Record<string, { name?: string }>)[offer.bot_id]
            : undefined;

          return (
            <OfferCard
              key={offer.offer_id}
              offer={offer}
              botName={offer.bot_id ? (bot?.name ?? offer.bot_id) : null}
              purchases={offerPurchases}
              reviews={offerReviews}
              agentModels={agentModels}
            />
          );
        })}
      </div>
    </div>
  );
}

function OfferCard({
  offer,
  botName,
  purchases,
  reviews,
  agentModels,
}: {
  offer: Offer;
  botName: string | null;
  purchases: Purchase[];
  reviews: Review[];
  agentModels: Record<string, string | null>;
}) {
  const [expanded, setExpanded] = useState(false);
  const files = offer.file_paths ?? [];

  return (
    <div className="offer-card panel">
      <div className="offer-card__top">
        <div>
          <strong className="offer-card__title">{offer.title}</strong>
          <div className="offer-card__submeta">
            <span>Seller: {offer.seller_agent_id}</span>
            <span>{botName ? `Bot: ${botName}` : "General bundle"}</span>
            {offer.status && <span>Status: {offer.status}</span>}
          </div>
        </div>
        <div className="offer-card__priceblock">
          <span className="offer-card__price">{offer.price_pct}%</span>
          <span className="offer-card__time">Updated {formatTime(offer.updated_at ?? offer.created_at)}</span>
        </div>
      </div>

      <p className="offer-card__desc">{offer.description || "No description provided."}</p>

      {offer.evidence && (
        <div className="offer-card__evidence">
          <button
            type="button"
            className="btn btn--sm"
            onClick={() => setExpanded((value) => !value)}
          >
            {expanded ? "Hide" : "Show"} Evidence
          </button>
          {expanded && <pre className="payload">{offer.evidence}</pre>}
        </div>
      )}

      {files.length > 0 && (
        <div className="offer-card__artifacts">
          <strong>Files</strong>
          <ul>
            {files.map((path, index) => (
              <li key={`${path}-${index}`}>{path}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="offer-card__stats">
        <span>{purchases.length} purchase{purchases.length !== 1 ? "s" : ""}</span>
        <span>{reviews.length} review{reviews.length !== 1 ? "s" : ""}</span>
        <span>Created {formatTime(offer.created_at)}</span>
      </div>

      <div className="offer-card__section">
        <strong>Purchases</strong>
        {purchases.length > 0 ? (
          purchases.map((purchase) => (
            <div key={purchase.purchase_id} className="review offer-card__purchase">
              <div className="offer-card__purchase-meta">
                <strong>{purchase.buyer_agent_id}</strong>
                <span className="offer-card__relation" data-relation={relationLabel(offer.seller_agent_id, purchase.buyer_agent_id, agentModels)}>
                  {relationLabel(offer.seller_agent_id, purchase.buyer_agent_id, agentModels)}
                </span>
                <span>{Number(purchase.price_pct ?? offer.price_pct).toFixed(1)}%</span>
                <span>{formatTime(purchase.created_at)}</span>
              </div>
            </div>
          ))
        ) : (
          <div className="review">
            <p>No buyers yet.</p>
          </div>
        )}
      </div>

      <div className="offer-card__section">
        <strong>Reviews</strong>
        {reviews.length > 0 ? (
          reviews.map((review) => (
            <div key={review.review_id} className="review">
              <div className="offer-card__purchase-meta">
                <strong>{review.buyer_agent_id}</strong>
                <span>{formatTime(review.created_at)}</span>
              </div>
              <p>{review.text}</p>
            </div>
          ))
        ) : (
          <div className="review">
            <p>No reviews yet.</p>
          </div>
        )}
      </div>
    </div>
  );
}
