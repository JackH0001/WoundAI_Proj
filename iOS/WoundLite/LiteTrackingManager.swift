import SwiftUI

/// Only opaque group identifiers are shared across devices. Locations and timelines remain on this device.
struct LiteTrackingManager: View {
  @ObservedObject var store: LiteStore
  let session: LiteNativeSession?
  @State private var roster: LiteWoundRoster?
  @State private var loaded = false
  @State private var busy = false
  @State private var notice: String?
  @State private var selection: LiteWoundGroup?
  @State private var ending = false
  @Environment(\.dismiss) private var dismiss

  private var groups: [LiteWoundGroup] {
    store.wounds + (roster?.remoteGroups(excluding: store.wounds) ?? [])
  }
  private func tracking(_ group: LiteWoundGroup) -> Bool {
    if let roster, let wire = try? LiteWoundRoster.wireID(group.id) {
      return roster.active.contains(wire)
    }
    return group.account == nil && !group.ended && store.localTrackingID == group.id
  }

  var body: some View {
    NavigationStack {
      List {
        Section {
          Text(
            roster?.summary
              ?? "免費可追蹤 1 處，Plus 每帳戶最多 5 處。")
          Text("結束追蹤只會釋出名額，歷史紀錄與 PDF 仍可使用。其他裝置只會取得傷口識別碼，照片與位置保存在原裝置。")
            .font(.footnote)
          Button("重新核對名額") { Task { await load() } }.disabled(busy)
          if let notice { Text(notice).font(.footnote).foregroundStyle(.secondary) }
        }
        ForEach(groups) { group in
          VStack(alignment: .leading, spacing: 6) {
            Text(group.trackingLabel)
            let isActive = tracking(group)
            if let roster, let wire = try? LiteWoundRoster.wireID(group.id), isActive,
              !roster.active.prefix(roster.limit).contains(wire)
            {
              Text("訂閱到期後暫停續記，歷史時間軸保留。").font(.footnote)
            }
            Button(isActive ? "結束追蹤" : "重新追蹤") {
              ending = isActive
              selection = group
            }.disabled(
              busy || !loaded || (group.account != nil && group.account != roster?.account_sha256))
          }
        }
      }.navigationTitle("管理追蹤傷口")
        .toolbar { Button("關閉") { dismiss() }.disabled(busy) }
        .interactiveDismissDisabled(busy)
        .task { await load() }
        .confirmationDialog(
          ending ? "結束追蹤這處傷口？" : "使用名額追蹤這處傷口？",
          isPresented: Binding(
            get: { selection != nil }, set: { if !$0 { selection = nil } }),
          titleVisibility: .visible,
          presenting: selection
        ) { group in
          Button(ending ? "確認結束追蹤" : "確認追蹤") { Task { await update(group, finish: ending) } }
          Button("取消", role: .cancel) {}
        } message: { group in
          Text(group.trackingLabel + "。原時間軸與照片會保留。")
        }
    }
  }

  private func load() async {
    guard !busy else { return }
    busy = true
    defer { busy = false }
    do {
      roster = try await session?.woundList()
      loaded = true
      notice = nil
    } catch {
      loaded = false
      roster = nil
      notice = LiteWoundTrackingError.unavailable.localizedDescription
    }
  }
  private func update(_ group: LiteWoundGroup, finish: Bool) async {
    guard !busy, !store.needsReload else { return }
    let accountGeneration = session?.identity.generation
    busy = true
    defer { busy = false }
    do {
      if finish {
        let owner = try await session?.finishTracking(group)
        guard accountGeneration == session?.identity.generation else { throw LiteWoundTrackingError.changed }
        if store.wounds.contains(where: { $0.id == group.id }),
          !store.finishWound(group.id, account: owner)
        {
          throw LiteWoundTrackingError.unavailable
        }
      } else {
        let permit = try await session?.woundPermit(
          id: group.id, owner: group.account, resume: true)
        guard accountGeneration == session?.identity.generation else { throw LiteWoundTrackingError.changed }
        guard store.resumeWound(group, permit: permit) else {
          if let error = store.storageError {
            notice = error
            return
          }
          throw LiteWoundTrackingError.unavailable
        }
      }
      roster = try await session?.woundList()
      notice = finish ? "已結束追蹤，原時間軸仍可查看、匯出。" : "已開始追蹤，可在量測時選擇這處傷口。"
    } catch {
      loaded = false
      notice = ((error as? LiteWoundTrackingError) ?? .unavailable).localizedDescription
    }
  }
}
