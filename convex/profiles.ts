import { mutation, query } from "./_generated/server";
import { v } from "convex/values";

export const list = query({
  args: {},
  handler: async (ctx) => {
    const profiles = await ctx.db
      .query("goaAgentProfiles")
      .order("desc")
      .collect();
    return profiles.map((p) => ({
      id: p._id,
      name: p.name,
      model: p.model,
      internetAccess: p.internetAccess,
      personality: p.personality,
      createdAt: p.createdAt,
      updatedAt: p.updatedAt,
    }));
  },
});

export const save = mutation({
  args: {
    name: v.string(),
    model: v.string(),
    internetAccess: v.boolean(),
    personality: v.string(),
  },
  handler: async (ctx, args) => {
    const now = Date.now();
    const existing = await ctx.db
      .query("goaAgentProfiles")
      .withIndex("by_name", (q) => q.eq("name", args.name))
      .first();
    if (existing) {
      await ctx.db.patch(existing._id, {
        model: args.model,
        internetAccess: args.internetAccess,
        personality: args.personality,
        updatedAt: now,
      });
      return existing._id;
    }
    return await ctx.db.insert("goaAgentProfiles", {
      ...args,
      createdAt: now,
      updatedAt: now,
    });
  },
});

export const remove = mutation({
  args: { id: v.id("goaAgentProfiles") },
  handler: async (ctx, args) => {
    await ctx.db.delete(args.id);
  },
});
