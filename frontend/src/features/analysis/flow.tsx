"use client";
import { createContext, useContext } from "react";
import type { AnalysisQuery } from "@/lib/business-analysis";
export interface ReviewFlow { query?: AnalysisQuery; resultId?: string; draftId?: string }
export const ReviewFlowContext = createContext<{ flow: ReviewFlow; remember: (patch: Partial<ReviewFlow>) => void; openHistory: () => void }>({ flow: {}, remember: () => {}, openHistory: () => {} });
export const useReviewFlow = () => useContext(ReviewFlowContext);
